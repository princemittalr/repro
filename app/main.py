"""Repro web server: the product UI plus a small JSON/SSE API.

    uvicorn app.main:app --reload        # then open http://localhost:8000

Endpoints:
    GET  /                          the landing page (app/static/landing.html)
    GET  /app                       the tool: upload or pick a sample, watch the live run (app/static/app.html)
    GET  /api/recent                real recent runs for the landing page (recorded demo runs + live runs)
    GET  /api/health                which integrations are configured, model routing, live-run budget
    GET  /api/samples               sample recordings (and whether a recorded run exists)
    POST /api/runs                  start a run: upload a video or pick a sample; live or replay
    GET  /api/runs/{id}             run summary, events so far, final result
    GET  /api/runs/{id}/events      live Server-Sent Events stream of pipeline events
    GET  /api/runs/{id}/video       the recording being analysed
    POST /api/perceive              PERCEIVE only (Phase 1 endpoint, kept for scripting)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.budget import budget
from app.config import PROJECT_ROOT, settings
from app.perception import PerceptionError, perceive
from app.runs import RunStore, recording_for, save_upload, stream
from app.showcase import recent_runs
from app.usage import PRICES_PER_M, UsageTracker
from app.video import VideoError

app = FastAPI(title="Repro", description="Show it the bug. It ships the fix.", version="0.4.0")

STATIC_DIR = Path(__file__).parent / "static"
SAMPLES_DIR = PROJECT_ROOT / "samples"
ALLOWED_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
MAX_UPLOAD_MB = 100

# The demo recordings, as shown on the start screen.
SAMPLES = {
    "coupon": ("Coupon stacks", "Applying SAVE10 twice doubles the discount."),
    "remove": ("Remove keeps the price", "Removing an item leaves its price in the subtotal."),
    "quantity": ("Quantity goes negative", "Pressing minus at 0 gives a negative quantity and price."),
}

runs = RunStore()
budget.enforced = True  # the public web server spends real credit: keep it inside the demo budget
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# The landing page can also be served as a static site (scripts/build_static.py) on a host with no
# cold start. It reads /api/health and /api/recent cross-origin; nothing else is opened up.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_credentials=False)


def _is_set(value: str) -> bool:
    """True when a secret has been filled in (not empty, not the .env.example placeholder)."""
    return bool(value) and "your" not in value


@app.get("/", include_in_schema=False, response_model=None)
def index(run: str | None = None) -> FileResponse | RedirectResponse:
    if run:  # run links shared before the tool moved to /app
        return RedirectResponse(f"/app?run={quote(run)}", status_code=308)
    return FileResponse(STATIC_DIR / "landing.html")


@app.get("/app", include_in_schema=False)
def tool() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/try", include_in_schema=False)
def try_it() -> RedirectResponse:
    return RedirectResponse("/app", status_code=308)


@app.get("/api/health")
def health() -> dict:
    """Report which integrations are configured (never the key values themselves)."""
    return {
        "status": "ok",
        "nebius_configured": _is_set(settings.nebius_api_key),
        "tavily_configured": _is_set(settings.tavily_api_key),
        "github_configured": _is_set(settings.github_token),
        "demo_repo": settings.github_demo_repo or None,
        "models": settings.models.as_dict(),
        "prices_known": bool(PRICES_PER_M),
        "budget": budget.status(),
    }


def _visitor(request: Request) -> str:
    """Best-effort visitor id: the client IP, as seen through the host's proxy."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/api/samples")
def samples() -> list[dict]:
    return [{"name": name, "title": title, "description": description,
             "video_url": f"/media/samples/{name}.mp4", "poster_url": f"/static/img/poster-{name}.jpg",
             "has_recording": recording_for(name) is not None}
            for name, (title, description) in SAMPLES.items() if (SAMPLES_DIR / f"shoplite-{name}.mp4").exists()]


@app.get("/api/recent")
def recent() -> list[dict]:
    """Real runs only: live runs this server finished, then the recorded demo runs. No text from
    uploaded recordings is included (see app/showcase.py)."""
    return recent_runs(SAMPLES)


@app.get("/media/samples/{name}.mp4", include_in_schema=False)
def sample_video(name: str) -> FileResponse:
    if name not in SAMPLES:
        raise HTTPException(404, "Unknown sample")
    return FileResponse(SAMPLES_DIR / f"shoplite-{name}.mp4", media_type="video/mp4")


@app.post("/api/runs")
async def start_run(
    request: Request,
    sample: str | None = Form(None, description="Name of a sample recording"),
    video: UploadFile | None = File(None, description="Screen recording of the bug"),
    mode: Literal["live", "replay"] = Form("live"),
    open_pr: bool = Form(False),
) -> dict:
    """Start a run on a sample or an uploaded video; returns the run id to stream."""
    if sample:
        if sample not in SAMPLES:
            raise HTTPException(404, "Unknown sample")
        path, name = SAMPLES_DIR / f"shoplite-{sample}.mp4", f"shoplite-{sample}.mp4"
        if mode == "replay":
            recording = recording_for(sample)
            if recording is None:
                raise HTTPException(404, "No recorded run for this sample yet; run it live instead.")
            return runs.start_replay(recording, path, name).summary()
    elif video is not None:
        suffix = Path(video.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(400, f"Unsupported file type '{suffix}'. Use one of: {', '.join(sorted(ALLOWED_SUFFIXES))}")
        path, name = save_upload(video.file, suffix), video.filename or f"recording{suffix}"
        if path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"Video is larger than {MAX_UPLOAD_MB} MB.")
    else:
        raise HTTPException(400, "Upload a video or choose a sample.")

    if not _is_set(settings.nebius_api_key):
        raise HTTPException(503, "NEBIUS_API_KEY is not configured, so live runs are unavailable.")
    allowed, pr_allowed, reason = budget.admit(_visitor(request), open_pr and _is_set(settings.github_token))
    if not allowed:
        raise HTTPException(429, reason)
    return runs.start_live(path, name, open_pr=pr_allowed, on_finish=budget.release).summary()


def _run_or_404(run_id: str):
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = _run_or_404(run_id)
    return {**run.summary(), "events": run.events, "result": run.result}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, last_event_id: str | None = Header(None)) -> StreamingResponse:
    run = _run_or_404(run_id)
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else -1
    return StreamingResponse(stream(run, start), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/video", include_in_schema=False)
def run_video(run_id: str) -> FileResponse:
    return FileResponse(_run_or_404(run_id).video)


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
