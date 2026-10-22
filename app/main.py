"""Repro web server.

Phase 1 adds the PERCEIVE endpoint. Later phases add the full agent
pipeline and the live SSE timeline UI.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.perception import PerceptionError, perceive
from app.usage import UsageTracker
from app.video import VideoError

app = FastAPI(title="Repro", description="Show it the bug. It ships the fix.", version="0.1.0")

ALLOWED_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
MAX_UPLOAD_MB = 100


def _is_set(value: str) -> bool:
    """True when a secret has been filled in (not empty, not the .env.example placeholder)."""
    return bool(value) and "your" not in value


@app.get("/")
def index() -> dict:
    """Placeholder home page until the real UI arrives in Phase 4."""
    return {"app": "Repro", "tagline": "Show it the bug. It ships the fix.", "health": "/api/health", "docs": "/docs"}


@app.get("/api/health")
def health() -> dict:
    """Report which integrations are configured (never the key values themselves)."""
    return {
        "status": "ok",
        "nebius_configured": _is_set(settings.nebius_api_key),
        "tavily_configured": _is_set(settings.tavily_api_key),
        "github_configured": _is_set(settings.github_token),
        "models": settings.models.as_dict(),
    }


@app.post("/api/perceive")
async def perceive_video(
    video: UploadFile = File(..., description="Screen recording of the bug (mp4, webm, mov, mkv)"),
    mode: Literal["hybrid", "video", "frames"] = Query("hybrid", description="How the video is sent to the model"),
) -> dict:
    """Watch an uploaded recording and return structured repro steps plus model usage."""
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Use one of: {', '.join(sorted(ALLOWED_SUFFIXES))}")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        with path.open("wb") as fh:
            shutil.copyfileobj(video.file, fh)
        if path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"Video is larger than {MAX_UPLOAD_MB} MB.")

        tracker = UsageTracker()
        try:
            report = await run_in_threadpool(perceive, path, mode, tracker)
        except VideoError as exc:
            raise HTTPException(400, f"Could not read the video: {exc}") from exc
        except PerceptionError as exc:
            raise HTTPException(502, str(exc)) from exc

    return {"report": report.model_dump(), "usage": tracker.summary()}
