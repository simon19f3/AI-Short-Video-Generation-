"""Local rendering, original backup score, subtitle timing, and film assembly."""

import math
import random
import shutil
import sys
import textwrap
import wave
from array import array
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont

from .core import PipelineError, ROOT, run


FPS = 24
SAMPLE_RATE = 24000
LEAD_IN = 0.25
FFMPEG = ("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_threads", "1", "-filter_complex_threads", "1")


def ffmpeg(*arguments, cwd=None):
    return run([*FFMPEG, *arguments], cwd=cwd)


def probe_duration(path):
    result = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=nokey=1:noprint_wrappers=1", path])
    length = float(result.strip())
    if not math.isfinite(length) or length <= 0:
        raise PipelineError(f"{path.name}: invalid media duration.")
    return length


def card(target, size, title, label="", color="#182a32", portrait_safe=False):
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    width, height = size
    column = max(12, int(width / max(15, min(width, height) * 0.054)))
    font = ImageFont.load_default(size=max(16, int(min(width, height) * 0.057)))
    lines = textwrap.fill(title, width=14 if portrait_safe else column)
    draw.multiline_text((width / 2, height * 0.43), lines, fill="#fff3da", font=font,
                        anchor="mm", align="center", spacing=8)
    if label:
        small = ImageFont.load_default(size=max(12, int(min(width, height) * 0.033)))
        draw.text((width / 2, height * 0.60), label, fill="#b1d7ce", font=small, anchor="mm")
    image.save(target)


def write_wave(path, samples):
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(samples.tobytes())


def silence(target, seconds):
    write_wave(target, array("h", [0]) * round(seconds * SAMPLE_RATE))


def synthesize_score(target, seconds, cues, seed):
    """A soft original instrumental built from a repeating piano-like motif."""
    rng = random.Random(seed)
    samples = array("h", [0]) * round(seconds * SAMPLE_RATE)
    tonic = rng.choice((48, 50, 53))
    beat = 60 / rng.choice((72, 76, 80))
    chords = ((0, 4, 7), (7, 11, 14), (9, 12, 16), (5, 9, 12))
    cue_index = 0

    def note(start, pitch, length, gain):
        frequency = 440 * 2 ** ((pitch - 69) / 12)
        offset = round(start * SAMPLE_RATE)
        for i in range(min(round(length * SAMPLE_RATE), len(samples) - offset)):
            t = i / SAMPLE_RATE
            envelope = min(t / 0.035, 1) * max(0, 1 - t / length) ** 2
            phase = 2 * math.pi * frequency * t
            sample = gain * envelope * (math.sin(phase) + 0.16 * math.sin(2 * phase))
            samples[offset + i] = max(-32768, min(32767, samples[offset + i] + round(sample)))

    for i in range(math.ceil(seconds / beat)):
        start = i * beat
        if cue_index + 1 < len(cues) and start >= cues[cue_index + 1]["start"]:
            cue_index += 1
        chord = chords[(i // 4) % len(chords)]
        index = (0, 2, 1, 2)[i % 4]
        note(start, tonic + chord[index] + 12, 1.6, 2450 if cue_index == 1 else 3150)
        if i % 4 == 0:
            note(start, tonic + chord[0], 2.7, 2800)
        if cue_index >= 2 and i % 2 == 0:
            note(start + beat / 2, tonic + chord[1] + 12, 1.8, 1500)
    write_wave(target, samples)


def fit_voice(target, source, speech_length, duration, speed):
    if source is None:
        silence(target, duration)
        return
    if speech_length / speed + LEAD_IN > duration:
        raise PipelineError("Narration is longer than its shot. Shorten the line or extend the shot.")
    delay_ms = int(LEAD_IN * 1000)
    ffmpeg("-i", source, "-af", f"atempo={speed},adelay={delay_ms}:all=1,apad",
           "-t", duration, "-ar", SAMPLE_RATE, "-ac", 1, "-c:a", "pcm_s16le", target)


def video_segment(target, source, size, seconds, mode):
    width, height = size
    frames = seconds * FPS
    contain = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101719")
    if mode == "wan":
        # Slow shorter generated clips across the slot, rather than holding a still frame.
        factor = seconds / probe_duration(source)
        filters = f"setpts=(PTS-STARTPTS)*{factor:.8f},fps={FPS},{contain}"
    elif mode == "panzoom":
        filters = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='1+0.075*on/{max(1, frames - 1)}':"
            f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={width}x{height}:fps={FPS}"
        )
    else:
        filters = f"{contain},zoompan=z=1:d={frames}:s={width}x{height}:fps={FPS}"
    ffmpeg("-i", source, "-vf", f"{filters},setsar=1", "-frames:v", frames, "-an",
           "-c:v", "libx264", "-threads", 2, "-preset", "fast", "-crf", 19, "-pix_fmt", "yuv420p", target)


def srt_time(seconds):
    milliseconds = round(seconds * 1000)
    minutes, ms = divmod(milliseconds, 60000)
    hours, minutes = divmod(minutes, 60)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def subtitle_cues(shots, speech_times):
    position = 0
    for shot, spoken_time in zip(shots, speech_times, strict=True):
        if shot["subtitle"].strip():
            start = position + LEAD_IN
            stop = position + min(shot["duration"], spoken_time + LEAD_IN + 0.15)
            yield start, stop, " ".join(shot["narration"].split()), " ".join(shot["subtitle"].split())
        position += shot["duration"]


def subtitles(cues, index, width):
    entries = []
    for number, (start, stop, *lines) in enumerate(cues, 1):
        line = textwrap.fill(lines[index], width=width)
        entries.append(f"{number}\n{srt_time(start)} --> {srt_time(stop)}\n{line}")
    return "\n\n".join(entries) + "\n" if entries else ""


def bilingual_subtitles(cues, size):
    width, height = size
    font_size = round(min(width, height) * 0.044)
    margin = round(height * 0.065)
    font = "Noto Sans Ethiopic"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Amharic,{font},{font_size},&H00DBF2FF,&H00FFFFFF,&H0010191D,&H5010191D,0,0,0,0,100,100,0,0,1,2,1,2,24,24,{margin},1
Style: English,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H0010191D,&H5010191D,0,0,0,0,100,100,0,0,1,2,1,2,24,24,{margin},1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = []
    for start, stop, amharic, english in cues:
        def ass_text(text):
            return text.replace("\\", "／").replace("{", "｛").replace("}", "｝")

        start_ass = srt_time(start)[1:-1].replace(",", ".")
        stop_ass = srt_time(stop)[1:-1].replace(",", ".")
        # A single stacked event prevents overlap and keeps the language order.
        lines = ass_text(amharic) + r"\N{\fs" + str(round(font_size * 0.35)) + r"}\h\N{\rEnglish}" + ass_text(english)
        events.append(f"Dialogue: 0,{start_ass},{stop_ass},Amharic,,0,0,0,,{lines}")
    return header + "\n".join(events) + "\n" if events else ""


def concat(segments, manifest, suffix):
    with manifest.open("w", encoding="utf-8") as stream:
        for index, source in enumerate(segments):
            local = manifest.parent / f"{manifest.stem}-{index}{suffix}"
            try:
                local.hardlink_to(source.resolve())
            except OSError:
                shutil.copyfile(source, local)
            stream.write(f"file '{local.name}'\n")


def mix_score(seconds):
    fade = min(2, seconds)
    return (
        "[1:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[narr][side];"
        "[2:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.28,"
        f"atrim=duration={seconds},afade=t=out:st={seconds-fade}:d={fade}[music];"
        "[music][side]sidechaincompress=threshold=0.035:ratio=8:attack=12:release=240[duck];"
        f"[narr][duck]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=duration={seconds}[mix]"
    )


def assemble(target, video_segments, voice_segments, score, content, seconds):
    with TemporaryDirectory() as folder:
        work = Path(folder)
        concat(video_segments, work / "video.txt", ".mp4")
        concat(voice_segments, work / "voice.txt", ".wav")
        if content:
            (work / "film.ass").write_text(content, encoding="utf-8")
            shutil.copytree(ROOT / "fonts", work / "fonts")
        video_filter = ("-vf", "subtitles=film.ass:fontsdir=fonts") if content else ()
        ffmpeg("-f", "concat", "-safe", "1", "-i", "video.txt",
               "-f", "concat", "-safe", "1", "-i", "voice.txt",
               "-stream_loop", -1, "-i", score.resolve(),
               "-filter_complex", mix_score(seconds), *video_filter,
               "-map", "0:v:0", "-map", "[mix]", "-t", seconds,
               "-c:v", "libx264", "-threads", 2, "-preset", "fast", "-crf", 19,
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", 48000, "-b:a", "160k",
               "-movflags", "+faststart", target.resolve(), cwd=work)
