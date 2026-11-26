"""FastAPI entry point for render jobs and playback."""

import os
import subprocess
import sys
from pathlib import Path
from threading import Lock, Thread
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, StrictBool


ROOT = Path(__file__).resolve().parents[1]
MEDIA = {"radio": "radio-cut.mp4", "horizontal": "horizontal.mp4", "vertical": "vertical.mp4"}
RenderKind = Literal["production", "preview"]
MediaName = Literal["radio", "horizontal", "vertical"]


class RenderRequest(BaseModel):
    action: Literal["film", "radio"]
    preview: StrictBool


class RenderJobs:
    def __init__(self):
        self.lock = Lock()
        self.state = "idle"
        self.action = None
        self.log = ""

    def snapshot(self):
        with self.lock:
            result = {"state": self.state, "action": self.action, "log": self.log}
        outputs = {}
        for kind in ("production", "preview"):
            outputs[kind] = {}
            for key, filename in MEDIA.items():
                target = ROOT / "outputs" / kind / filename
                try:
                    entry = target.stat()
                    url = f"/api/media/{kind}/{key}?v={entry.st_mtime_ns}" if entry.st_size else None
                except FileNotFoundError:
                    url = None
                outputs[kind][key] = url
        return {**result, "outputs": outputs}

    def launch(self, job):
        with self.lock:
            if self.state == "running":
                raise HTTPException(status_code=409, detail="A render is already running.")
            self.state, self.action, self.log = "running", job.action, ""
            command = [sys.executable, "-u", "-m", "pipeline", job.action]
            if job.preview:
                command.append("--preview")
            try:
                child = subprocess.Popen(
                    command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
            except OSError as exc:
                self.state, self.log = "error", f"Could not start Python: {exc}"
                raise HTTPException(status_code=500, detail="Could not start the render.") from exc
            Thread(target=self.collect, args=(child,), daemon=True).start()

    def collect(self, child):
        with child.stdout as stream:
            for line in stream:
                with self.lock:
                    self.log = (self.log + line)[-12000:]
        code = child.wait()
        with self.lock:
            self.state = "done" if code == 0 else "error"


app = FastAPI(title="Graduation Film Studio API")
jobs = RenderJobs()


@app.get("/api/jobs")
def render_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return jobs.snapshot()


@app.post("/api/jobs", status_code=status.HTTP_202_ACCEPTED)
def start_render(job: RenderRequest, request: Request):
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Open the studio on its local address.")
    jobs.launch(job)
    return jobs.snapshot()


@app.get("/api/media/{kind}/{name}", response_class=FileResponse)
def get_video(kind: RenderKind, name: MediaName):
    target = ROOT / "outputs" / kind / MEDIA[name]
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Render not found.")
    return FileResponse(target, media_type="video/mp4", headers={"Cache-Control": "private, no-cache"})
