"""Adapters for Google AI Studio, Cloudflare, self-hosted ACE-Step, and Wan."""

import base64
import io
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image

from .core import PipelineError, atomic_write, run


TTS_MODEL = "gemini-3.8-flash-tts"
TEXT_MODEL = "gemini-3.8-flash"
FLUX_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
MUSIC_MODEL = "acestep-v15-turbo"
WAN_MODEL = "Wan2.2-TI2V-5B"
GOOGLE = "https://generativelanguage.googleapis.com/v1beta/interactions"


def required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise PipelineError(f"Set {name} in .env and try again.")
    return value


def request(method, url, **kwargs):
    response = requests.request(method, url, timeout=kwargs.pop("timeout", (10, 120)), **kwargs)
    response.raise_for_status()
    return response


def gemini_blocks(payload):
    response = request(
        "POST", GOOGLE, headers={"x-goog-api-key": required("GEMINI_API_KEY")}, json=payload
    ).json()
    return [block for step in response.get("steps", []) for block in step.get("content", [])], response.get("usage")


def narrate(target, params):
    blocks, usage = gemini_blocks({
        "model": params["model"], "store": False,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": params["text"],
            "annotations": [{"type": "speech_metadata", "style": params["delivery"]}],
        }]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": params["voice"]}]},
    })
    for block in reversed(blocks):
        if block.get("type") == "audio" and block.get("data"):
            audio = base64.b64decode(block["data"], validate=True)
            if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                raise PipelineError("Gemini returned audio in an unexpected format; WAV is required.")
            target.write_bytes(audio)
            return {"usage": usage}
    raise PipelineError("Gemini returned no audio.")


def draft(target, params, schema):
    blocks, usage = gemini_blocks({
        "model": params["model"], "store": False, "input": params["prompt"],
        "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
    })
    chunks = [block["text"] for block in blocks if block.get("type") == "text" and "text" in block]
    data = json.loads("".join(chunks))
    atomic_write(target, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"usage": usage}


def keyframe(target, params, references):
    account = required("CLOUDFLARE_ACCOUNT_ID")
    token = required("CLOUDFLARE_API_TOKEN")
    fields = {name: (None, str(params[name])) for name in ("prompt", "seed", "width", "height")}
    for index, reference in enumerate(references):
        mime = "image/png" if reference.suffix.lower() == ".png" else "image/jpeg"
        fields[f"input_image_{index}"] = (reference.name, reference.read_bytes(), mime)
    response = request(
        "POST", f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{FLUX_MODEL}",
        headers={"Authorization": f"Bearer {token}"}, files=fields,
    )
    if response.headers.get("content-type", "").startswith("image/"):
        image_bytes = response.content
    else:
        data = response.json()
        if not data.get("success") or not data.get("result", {}).get("image"):
            raise PipelineError("Cloudflare did not return an image. Check model access and quota.")
        image_bytes = base64.b64decode(data["result"]["image"], validate=True)
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.convert("RGB").save(target)


def music(target, params, base_url):
    key = os.getenv("ACESTEP_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def call(path, payload):
        body = request("POST", f"{base_url}{path}", headers=headers, json=payload).json()
        if body.get("code") != 200 or not body.get("data"):
            raise PipelineError(f"ACE-Step returned an unsuccessful response from {path}.")
        return body["data"]

    job = call("/release_task", {
        "model": MUSIC_MODEL, "prompt": params["prompt"], "lyrics": "[Instrumental]",
        "task_type": "text2music", "duration": params["duration"], "audio_duration": params["duration"],
        "audio_format": "wav", "thinking": False, "use_random_seed": False,
        "batch_size": 1, "seed": params["seed"], "inference_steps": 8,
    })
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        rows = call("/query_result", {"task_id_list": [job["task_id"]]})
        status = rows[0]["status"]
        if status == 2:
            raise PipelineError("ACE-Step reported a failed generation job.")
        if status == 1:
            result = json.loads(rows[0]["result"])
            path = result[0]["file"]
            parsed = urlsplit(path)
            if parsed.scheme or parsed.netloc or parsed.path != "/v1/audio":
                raise PipelineError("ACE-Step returned an unexpected audio download URL.")
            response = request("GET", f"{base_url}{path}", headers=headers)
            audio = response.content
            if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                raise PipelineError("ACE-Step did not return the requested WAV file.")
            target.write_bytes(audio)
            return
        time.sleep(3)
    raise PipelineError("ACE-Step job timed out after 15 minutes.")


def wan_config():
    names = ("WAN_PYTHON", "WAN_REPO", "WAN_CHECKPOINT")
    values = [os.getenv(name, "").strip() for name in names]
    if not any(values):
        return None
    if not all(values):
        raise PipelineError("Set WAN_PYTHON, WAN_REPO and WAN_CHECKPOINT together in .env.")
    python, repo, checkpoint = map(lambda item: Path(item).expanduser().resolve(), values)
    if not python.is_file() or not (repo / "generate.py").is_file() or not checkpoint.is_dir():
        raise PipelineError("Check WAN_PYTHON, WAN_REPO and WAN_CHECKPOINT paths in .env.")
    return python, repo, checkpoint


def animate(target, params, source, config):
    python, repo, checkpoint = config
    started = time.monotonic()
    run([
        python, repo / "generate.py", "--task", "ti2v-5B", "--size", params["size"],
        "--ckpt_dir", checkpoint, "--image", source.resolve(), "--prompt", params["prompt"],
        "--frame_num", params["frames"],
        "--base_seed", params["seed"], "--save_file", target.resolve(),
        "--offload_model", "True", "--convert_model_dtype", "--t5_cpu",
    ], cwd=repo, timeout=int(os.getenv("WAN_TIMEOUT", "1800")))
    if target.stat().st_size == 0:
        raise PipelineError("Wan completed without creating a video.")
    completed = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                     "stream=duration", "-of", "default=nw=1:nk=1", target])
    if float(completed.strip()) < 2:
        raise PipelineError("Wan generated less than two seconds of usable footage.")
    return {"gpu_job_wall_minutes": round((time.monotonic() - started) / 60, 3)}
