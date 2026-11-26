"""Storyboard validation, media cache, and execution records."""

import hashlib
import json
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import jsonschema
import requests


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "storyboard.schema.json"


class PipelineError(Exception):
    """An error the command-line interface can present directly."""


def load_story(path):
    story = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(story, schema)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        raise PipelineError(f"{location or path.name}: {exc.message}") from exc

    ids = [shot["id"] for shot in story["shots"]]
    if len(set(ids)) != len(ids):
        raise PipelineError("Every shot must have a unique id.")
    if not 60 <= duration(story) <= 90:
        raise PipelineError("The film, including the end card, must be 60–90 seconds.")
    earlier_shots = set()
    for shot in story["shots"]:
        unknown = set(shot["references"]) - story["references"].keys()
        if unknown:
            raise PipelineError(f"{shot['id']}: unknown references {sorted(unknown)}.")
        start = shot.get("start_frame")
        if start and not story["references"].get(start, {}).get("direct"):
            raise PipelineError(f"{shot['id']}: start_frame must name a direct photo reference.")
        continuity = shot.get("continuity_from")
        if continuity and (continuity not in earlier_shots or len(shot["references"]) >= 4 or start):
            raise PipelineError(f"{shot['id']}: continuity_from needs an earlier shot and an open reference slot.")
        earlier_shots.add(shot["id"])
        if bool(shot["narration"].strip()) != bool(shot["subtitle"].strip()):
            raise PipelineError(f"{shot['id']}: supply both narration and the English subtitle, or leave both empty.")
    music = story["music"]
    starts = [cue["start"] for cue in music["cues"]]
    if starts[0] != 0 or starts != sorted(set(starts)) or starts[-1] >= duration(story):
        raise PipelineError("Music cues must start at 0 and increase within the film's duration.")
    if music["selected"] >= len(music["seeds"]):
        raise PipelineError("music.selected must index a seed in music.seeds.")
    if story.get("require_motion") and any(shot["motion"] != "wan" for shot in story["shots"]):
        raise PipelineError("Every shot must use Wan when require_motion is set.")
    for ref in story["references"].values():
        if ref.get("direct") and not ref.get("source"):
            raise PipelineError("A direct reference must have a source photo.")
    return story


def duration(story):
    return sum(shot["duration"] for shot in story["shots"]) + story["end_card"]["duration"]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def run(args, *, cwd=None, timeout=600):
    try:
        return subprocess.run(
            [str(arg) for arg in args], cwd=cwd, timeout=timeout,
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip()[-1800:]
        raise PipelineError(f"{Path(args[0]).name} failed: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"{Path(args[0]).name} timed out after {timeout}s.") from exc


class Cache:
    def __init__(self, root):
        self.root = root

    def get(self, stage, item, inputs, suffix, generate):
        key = digest({"version": 1, "inputs": inputs})
        target = self.root / stage / f"{item}-{key}{suffix}"
        if target.is_file() and target.stat().st_size:
            print(f"hit   {stage}/{item}", flush=True)
            return target
        print(f"build {stage}/{item}", flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, suffix=suffix, delete=False) as stream:
            temp = Path(stream.name)
        try:
            generate(temp)
            if not temp.stat().st_size:
                raise PipelineError(f"{stage}/{item}: empty output.")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        return target


class RunLog:
    def __init__(self, path):
        self.path = path
        self.run_id = os.urandom(6).hex()

    def write(self, **event):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event.update(timestamp=datetime.now(timezone.utc).isoformat(), run_id=self.run_id)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(canonical(event) + "\n")

    @contextmanager
    def stage(self, name):
        started = time.monotonic()
        status = "error"
        try:
            yield
            status = "ok"
        finally:
            self.write(event="stage_timing", stage=name, status=status,
                       latency_ms=round((time.monotonic() - started) * 1000))

    def model(self, stage, item, model, inputs, operation, attempts=3):
        # Keep private text and file contents out of the public log.
        private = {"prompt", "text", "delivery", "lyrics"}
        params = {key: value for key, value in inputs.items() if key not in private}
        record = dict(
            event="model_call", stage=stage, item=item, model=model, parameters=params,
            input_hash=digest(inputs), seed=inputs.get("seed"), cost_usd=None,
            gpu_minutes=None,
        )
        if model in {"Wan2.2-TI2V-5B", "acestep-v15-turbo"}:
            record["cost_usd"] = 0.0  # Model/API fee for self-hosting. Compute is separate.
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                metrics = operation() or {}
            except (requests.RequestException, ValueError, KeyError, TypeError, PipelineError) as exc:
                response = getattr(exc, "response", None)
                status = response.status_code if response is not None else None
                self.write(
                    **record, attempt=attempt, latency_ms=round((time.monotonic() - started) * 1000),
                    status="error", error_type=type(exc).__name__, http_status=status,
                )
                transient = status in {408, 429, 500, 502, 503, 504} or isinstance(
                    exc, (requests.ConnectionError, requests.Timeout)
                )
                if attempt == attempts or not transient:
                    detail = f"HTTP {status}" if status is not None else str(exc)
                    if isinstance(exc, requests.RequestException) and status is None:
                        detail = type(exc).__name__
                    raise PipelineError(f"{stage}/{item} ({model}): {detail}") from exc
                time.sleep(2 ** attempt)
            else:
                record.update(metrics)
                self.write(
                    **record, attempt=attempt, latency_ms=round((time.monotonic() - started) * 1000),
                    status="ok",
                )
                return
