"""Command-line entry point for the heritage short."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import media, providers
from .core import Cache, PipelineError, ROOT, SCHEMA, RunLog, atomic_write, duration, file_hash, load_story, run


class Film:
    def __init__(self, story, output, cache, log, preview):
        self.story = story
        self.output = output
        self.cache = cache
        self.log = log
        self.preview = preview
        self.seconds = duration(story)
        self.shots = story["shots"]

    def narration(self, shots):
        tracks, speech_times = [], []
        for shot in shots:
            shot_id, seconds = shot["id"], shot["duration"]
            source, speed, spoken = None, 1.0, 0.0
            text = shot["narration"].strip()
            if text and self.preview:
                spoken = seconds - 0.55
            if text and not self.preview:
                model = os.getenv("GEMINI_TTS_MODEL") or providers.TTS_MODEL
                params = {"model": model, **self.story["voice"], "text": text}
                source = self.cache.get(
                    "narration", shot_id, params, ".wav",
                    lambda target: self.log.model(
                        "narration", shot_id, model, params, lambda: providers.narrate(target, params)
                    ),
                )
                spoken = media.probe_duration(source)
                speed = max(1.0, spoken / (seconds - media.LEAD_IN - 0.12))
                if speed > 1.18:
                    raise PipelineError(f"{shot_id}: the narration is {spoken:.1f}s for a {seconds}s shot. Shorten the line or extend the shot.")
                spoken /= speed
            fitted = self.cache.get(
                "voice-timeline", shot_id,
                {"source": file_hash(source) if source else None, "duration": seconds, "speed": speed},
                ".wav", lambda target: media.fit_voice(target, source, spoken * speed, seconds, speed),
            )
            tracks.append(fitted)
            speech_times.append(spoken)
        return tracks, speech_times

    def images(self, shots):
        references = {}
        frames = {}
        style = self.story["style"]
        for shot in shots:
            shot_id = shot["id"]
            if self.preview:
                frames[shot_id] = self.cache.get(
                    "preview-image", shot_id, {"title": shot["title"], "portrait_safe": True}, ".png",
                    lambda target: media.card(target, (1024, 768), shot["title"], "OFFLINE PREVIEW",
                                              portrait_safe=True),
                )
                continue
            if shot.get("start_frame"):
                frames[shot_id] = self.photo(self.story["references"][shot["start_frame"]])
                continue
            for ref_id in shot["references"]:
                if ref_id in references:
                    continue
                ref = self.story["references"][ref_id]
                source = self.photo(ref) if ref.get("source") else None
                if ref.get("direct"):
                    references[ref_id] = source
                    continue
                params = {"model": providers.FLUX_MODEL, "seed": ref["seed"], "width": 768,
                          "height": 768, "prompt": f"{style}\n{ref['prompt']}"}
                if source:
                    params["reference_hash"] = file_hash(source)
                references[ref_id] = self.flux("references", ref_id, params, [source] if source else [])
            refs = [references[ref_id] for ref_id in shot["references"]]
            direction = "Use the supplied references for identity and any setting explicitly mentioned." if refs else ""
            continuity = shot.get("continuity_from")
            if continuity:
                refs.append(frames[continuity])
                direction += " Preserve the supporting characters from the last reference, adjusting age if specified."
            params = {
                "model": providers.FLUX_MODEL, "width": 1024, "height": 768, "seed": shot["seed"],
                "references": [file_hash(ref) for ref in refs],
                "prompt": f"{style}\n{direction}\n{shot['image_prompt']}",
            }
            frames[shot_id] = self.flux("keyframes", shot_id, params, refs)
        return list(frames.values())

    def photo(self, ref):
        if os.getenv("ALLOW_PERSONAL_REFERENCES") != "1":
            raise PipelineError("Record permission in ETHICS.md, then set ALLOW_PERSONAL_REFERENCES=1 in .env.")
        path = ROOT / "private-references" / ref["source"]
        if not path.is_file():
            raise PipelineError(f"Missing reference photo: {path.name} in private-references.")
        return path

    def flux(self, stage, item, params, references):
        return self.cache.get(
            stage, item, params, ".png", lambda target: self.log.model(
                stage, item, providers.FLUX_MODEL, params,
                lambda: providers.keyframe(target, params, references),
            ),
        )

    def motion(self, shots, frames):
        settings = providers.wan_config() if not self.preview and any(s["motion"] == "wan" for s in shots) else None
        clips = []
        for shot, frame in zip(shots, frames, strict=True):
            if shot["motion"] == "static":
                clips.append((frame, "static"))
                continue
            if shot["motion"] == "panzoom" or self.preview:
                clips.append((frame, "panzoom"))
                continue
            shot_id = shot["id"]
            if settings:
                params = {
                    "model": providers.WAN_MODEL, "image": file_hash(frame),
                    "prompt": shot["motion_prompt"], "seed": shot["seed"],
                    "size": os.getenv("WAN_SIZE", "1280*704"),
                    "frames": 4 * (min(shot["duration"], 6) * media.FPS // 4) + 1,
                    "checkpoint": settings[2].name,
                    "version": os.getenv("WAN_MODEL_REVISION", "local-v1"),
                }
                try:
                    clip = self.cache.get(
                        "wan", shot_id, params, ".mp4",
                        lambda target: self.log.model(
                            "motion", shot_id, providers.WAN_MODEL, params,
                            lambda: providers.animate(target, params, frame, settings), attempts=1,
                        ),
                    )
                    clips.append((clip, "wan"))
                    continue
                except PipelineError as exc:
                    if self.story.get("require_motion"):
                        raise
                    print(f"{shot_id}: Wan failed. Using pan-and-zoom. {exc}", file=sys.stderr)
                reason = "wan_failed"
            else:
                reason = "wan_unconfigured"
            self.log.write(event="fallback", stage="motion", item=shot_id, provider="ffmpeg", reason=reason)
            clips.append((frame, "panzoom"))
        return clips

    def score(self, index, *, radio=False):
        story_music = self.story["music"]
        seed = story_music["seeds"][index]
        cues = story_music["cues"]
        endpoint = (os.getenv("ACESTEP_URL") or "").strip().rstrip("/")
        if not endpoint or self.preview or radio:
            if not radio and not self.preview:
                self.log.write(event="fallback", stage="music", item=f"take-{index+1}",
                               provider="local_original_score", reason="acestep_unconfigured")
            return self.cache.get(
                "original-score", f"take-{index+1}", {"duration": self.seconds, "cues": cues, "seed": seed},
                ".wav", lambda target: media.synthesize_score(target, self.seconds, cues, seed),
            )
        if not endpoint.startswith(("http://", "https://")):
            raise PipelineError("ACESTEP_URL must begin with http:// or https://.")
        description = " ".join(f"At {cue['start']} seconds: {cue['direction']}." for cue in cues)
        params = {
            "model": providers.MUSIC_MODEL, "duration": self.seconds, "seed": seed,
            "prompt": f"Original instrumental underscore, no vocals. {description}",
            "revision": os.getenv("ACESTEP_MODEL_REVISION", "local-v1"), "inference_steps": 8,
        }
        return self.cache.get(
            "ace-step", f"take-{index+1}", params, ".wav",
            lambda target: self.log.model(
                "music", f"take-{index+1}", providers.MUSIC_MODEL, params,
                lambda: providers.music(target, params, endpoint), attempts=1,
            ),
        )

    def segment(self, item, source, mode, size, seconds):
        return self.cache.get(
            "video-segment", item,
            {"source": file_hash(source), "mode": mode, "size": size, "duration": seconds, "fps": media.FPS,
             "framing": "contain-stills-v1"},
            ".mp4", lambda target: media.video_segment(target, source, size, seconds, mode),
        )

    def deliver(self, video, voice, speech, music, *, radio=False):
        self.output.mkdir(parents=True, exist_ok=True)
        end = self.story["end_card"]
        voice_end = self.cache.get("voice-timeline", "end-card", end["duration"], ".wav",
                                   lambda target: media.silence(target, end["duration"]))
        layouts = {"radio-cut": (640, 360)} if radio else (
            {"horizontal": (640, 360), "vertical": (360, 640)} if self.preview else
            {"horizontal": (1280, 720), "vertical": (720, 1280)}
        )
        outputs = {}
        for label, size in layouts.items():
            if radio:
                black = self.cache.get("titles", "radio-black", size, ".png",
                                       lambda target: media.card(target, size, "", color="#000000"))
                segments = [self.segment("radio", black, "static", size, self.seconds)]
                content = ""
            else:
                segments = [
                    self.segment(shot["id"], source, mode, size, shot["duration"])
                    for (source, mode), shot in zip(video, self.shots, strict=True)
                ]
                end_card = self.cache.get(
                    "titles", "end-card", {"text": end["text"], "title": self.story["title"], "size": size},
                    ".png", lambda target: media.card(target, size, end["text"], self.story["title"]),
                )
                segments.append(self.segment("end-card", end_card, "static", size, end["duration"]))
                cues = list(media.subtitle_cues(self.shots, speech))
                content = media.bilingual_subtitles(cues, size)
                for index, language in enumerate(("am", "en")):
                    sidecar = media.subtitles(cues, index, 30 if label == "vertical" else 55)
                    atomic_write(self.output / "subtitles" / f"{label}.{language}.srt", sidecar.encode("utf-8"))
            fitted = [*voice, voice_end]
            render = self.cache.get(
                "assembly", label, {"video": [file_hash(p) for p in segments],
                                    "voice": [file_hash(p) for p in fitted], "music": file_hash(music),
                                    "subtitles": content, "seconds": self.seconds},
                ".mp4", lambda target: media.assemble(target, segments, fitted, music, content, self.seconds),
            )
            final = self.output / f"{label}.mp4"
            if not final.exists() or file_hash(final) != file_hash(render):
                atomic_write(final, render.read_bytes())
            outputs[label] = {"filename": final.name, "duration_seconds": media.probe_duration(final)}
            print(final)
        if not radio:
            manifest = {
                "title": self.story["title"], "preview": self.preview, "expected_duration_seconds": self.seconds,
                "motion_by_shot": {shot["id"]: mode for shot, (_, mode) in zip(self.shots, video, strict=True)},
                "score": music.parent.name, "outputs": outputs, "run_id": self.log.run_id,
            }
            atomic_write(self.output / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))

    def music_candidates(self):
        for index in range(len(self.story["music"]["seeds"])):
            score = self.score(index)
            target = self.output / "music" / f"take-{index+1}.wav"
            atomic_write(target, score.read_bytes())
            print(target)


def doctor():
    for program in ("ffmpeg", "ffprobe"):
        if not shutil.which(program):
            raise PipelineError(f"{program} is missing from PATH.")
    filters = run(["ffmpeg", "-hide_banner", "-filters"])
    missing = [name for name in ("subtitles", "zoompan", "sidechaincompress", "loudnorm") if name not in filters]
    if missing:
        raise PipelineError(f"Install a full FFmpeg build with these filters: {', '.join(missing)}.")
    encoders = run(["ffmpeg", "-hide_banner", "-encoders"])
    if "libx264" not in encoders:
        raise PipelineError("Install an FFmpeg build with libx264.")
    for name in ("GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "ACESTEP_URL", "WAN_CHECKPOINT"):
        print(f"{name}: {'set' if os.getenv(name) else 'not set'}")
    print("Local media tools are ready.")


def draft_schema(node):
    """Use Gemini's subset, then apply the full schema locally to its result."""
    if not isinstance(node, dict):
        return node
    omitted = {"$schema", "propertyNames", "pattern", "uniqueItems"}
    return {key: draft_schema(value) for key, value in node.items() if key not in omitted}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "doctor", "smoke", "draft", "radio", "music", "film"))
    parser.add_argument("--storyboard", type=Path, default=ROOT / "storyboard.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preview", action="store_true", help="Offline mode with placeholder images and silent voice.")
    parser.add_argument("--idea", help="New story idea for the draft command.")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if args.command == "doctor":
        doctor()
        return

    story = load_story(args.storyboard.resolve())
    if args.command == "validate":
        print(f"{story['title']} · {len(story['shots'])} shots · {duration(story)} seconds including end card")
        return

    kind = "preview" if args.preview else "production"
    output = (args.output or ROOT / "outputs" / kind).resolve()
    log = ROOT / ".cache" / "preview" / "runs.jsonl" if args.preview else ROOT / "runs.jsonl"
    film = Film(story, output, Cache(ROOT / ".cache" / kind), RunLog(log), args.preview)
    if args.command == "draft":
        if not args.idea or args.preview:
            raise PipelineError("Use draft --idea with Gemini configured.")
        target = output / "storyboard.draft.json"
        if target.resolve() == args.storyboard.resolve():
            raise PipelineError("Choose an output directory that keeps the input storyboard separate.")
        params = {"model": providers.TEXT_MODEL, "prompt": (
            f"Draft a storyboard for this idea: {args.idea}\nUse the exact structure and data types "
            "in the example below. Write 8–12 shots, a 60–90 second total including the end card, "
            "Amharic narration and English subtitles unless requested otherwise, and mark real details "
            f"for author review without inventing personal facts. Return JSON only.\n{json.dumps(story, ensure_ascii=False)}"
        )}
        schema = draft_schema(json.loads(SCHEMA.read_text(encoding="utf-8")))
        film.log.model(
            "storyboard", "draft", providers.TEXT_MODEL, params, lambda: providers.draft(target, params, schema),
        )
        load_story(target)
        print(f"Draft written to {target}. Review before replacing your approved storyboard.")
        return

    if args.command == "music":
        with film.log.stage("music_candidates"):
            film.music_candidates()
        return

    if args.command == "smoke":
        shot = next((shot for shot in film.shots if shot["narration"].strip()), film.shots[0])
        film.narration([shot])
        film.images([shot])
        print("Narration and keyframe checks passed. Use the music command to test your selected score runner.")
        return

    if args.command == "film" and not args.preview and story.get("require_motion"):
        if not providers.wan_config():
            raise PipelineError("This storyboard requires video inside each shot. Configure WAN_PYTHON, WAN_REPO and WAN_CHECKPOINT in .env.")
        for ref in story["references"].values():
            if ref.get("source"):
                film.photo(ref)

    with film.log.stage("narration"):
        voice, speech = film.narration(film.shots)
    if args.command == "radio":
        with film.log.stage("music_placeholder"):
            score = film.score(0, radio=True)
        with film.log.stage("radio_assembly"):
            film.deliver([], voice, speech, score, radio=True)
        return

    with film.log.stage("keyframes"):
        images = film.images(film.shots)
    with film.log.stage("motion"):
        shots = film.motion(film.shots, images)
    with film.log.stage("music"):
        score = film.score(story["music"]["selected"])
    with film.log.stage("assembly"):
        film.deliver(shots, voice, speech, score)


if __name__ == "__main__":
    try:
        main()
    except (PipelineError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
