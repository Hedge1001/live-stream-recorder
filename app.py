"""Live Stream Recorder API."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

CAPTURE_DIR = Path(os.getenv("CAPTURE_DIR", "captures")).resolve()
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
MAX_STREAMS = int(os.getenv("MAX_CONCURRENT_STREAMS", "5"))
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

jobs: dict[str, dict[str, Any]] = {}
lock = threading.Lock()


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return value.strip("-._")[:80] or "stream"


def run_ffmpeg(stream_id: str, source: str, output: Path) -> None:
    command = [FFMPEG_BIN, "-hide_banner", "-loglevel", "warning", "-y", "-i", source, "-c", "copy", "-movflags", "+faststart", str(output)]
    try:
        process = subprocess.Popen(command, start_new_session=True)
        with lock:
            jobs[stream_id]["pid"] = process.pid
        return_code = process.wait()
        with lock:
            if stream_id in jobs:
                jobs[stream_id]["status"] = "completed" if return_code == 0 else "failed"
                jobs[stream_id]["return_code"] = return_code
    except FileNotFoundError:
        with lock:
            jobs[stream_id]["status"] = "failed"
            jobs[stream_id]["error"] = "FFmpeg was not found"
    except Exception as exc:
        with lock:
            jobs[stream_id]["status"] = "failed"
            jobs[stream_id]["error"] = str(exc)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "capture_dir": str(CAPTURE_DIR)})


@app.get("/api/streams")
def list_streams():
    with lock:
        return jsonify(list(jobs.values()))


@app.post("/api/streams")
def start_stream():
    payload = request.get_json(silent=True) or {}
    name, source = payload.get("name"), payload.get("source")
    if not isinstance(name, str) or not isinstance(source, str) or not name or not source:
        return jsonify({"error": "name and source are required"}), 400
    with lock:
        active = sum(job["status"] == "recording" for job in jobs.values())
        if active >= MAX_STREAMS:
            return jsonify({"error": "maximum concurrent streams reached"}), 409
        stream_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        day_dir = CAPTURE_DIR / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        output = day_dir / f"{safe_name(name)}-{now.strftime('%H%M%S')}-{stream_id[:8]}.mp4"
        job = {"id": stream_id, "name": name, "source": source, "output": str(output.relative_to(CAPTURE_DIR)), "started_at": now.isoformat(), "status": "recording"}
        jobs[stream_id] = job
    threading.Thread(target=run_ffmpeg, args=(stream_id, source, output), daemon=True).start()
    return jsonify(job), 202


@app.delete("/api/streams/<stream_id>")
def stop_stream(stream_id: str):
    with lock:
        job = jobs.get(stream_id)
        if not job:
            return jsonify({"error": "stream not found"}), 404
        pid = job.get("pid")
        if job["status"] == "recording" and pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            job["status"] = "stopping"
        return jsonify(job)


@app.get("/api/recordings")
def recordings():
    files = [{"name": str(path.relative_to(CAPTURE_DIR)), "size": path.stat().st_size} for path in CAPTURE_DIR.rglob("*.mp4") if path.is_file()]
    return jsonify(files)


@app.get("/captures/<path:filename>")
def download_capture(filename: str):
    return send_from_directory(CAPTURE_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "5000")))
