"""Pipeline runs for the web UI: start them in the background, stream their events.

A run is either LIVE (the real pipeline in a worker thread) or a REPLAY of a
recorded run (the saved events re-emitted with their original pacing, sped
up). Replays make the demo instant and reliable, and work offline; the UI
always labels them as replays.

Events are kept in memory and streamed to any number of browsers as
Server-Sent Events. A browser that reconnects passes `Last-Event-ID` and only
gets what it missed.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable, Literal

from app.config import PROJECT_ROOT
from app.events import EventBus, PipelineEvent
from app.llm import friendly_error

RUNS_DIR = PROJECT_ROOT / "runs" / "pipeline"
DEMO_DIR = PROJECT_ROOT / "demo"         # recorded runs that ship with the repo, one per sample
UPLOADS_DIR = PROJECT_ROOT / "uploads"
REPLAY_MAX_GAP_S = 1.5                   # long waits (model calls) are shortened in replays
KEEPALIVE_S = 15


@dataclass
class Run:
    id: str
    video: Path
    video_name: str
    mode: Literal["live", "replay"]
    open_pr: bool
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    finished: bool = False
    result: dict | None = None
    recorded_duration_s: float | None = None  # replays: how long the original live run took

    def summary(self) -> dict:
        return {"id": self.id, "video_name": self.video_name, "mode": self.mode, "open_pr": self.open_pr,
                "created": self.created, "finished": self.finished, "events": len(self.events),
                "recorded_duration_s": self.recorded_duration_s}


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def _new(self, video: Path, video_name: str, mode: Literal["live", "replay"], open_pr: bool) -> Run:
        run = Run(id=uuid.uuid4().hex[:12], video=video, video_name=video_name, mode=mode, open_pr=open_pr)
        with self._lock:
            self._runs[run.id] = run
        return run

    def start_live(self, video: Path, video_name: str, open_pr: bool,
                   on_finish: Callable[[], None] | None = None) -> Run:
        run = self._new(video, video_name, "live", open_pr)
        threading.Thread(target=self._live, args=(run, on_finish), daemon=True, name=f"run-{run.id}").start()
        return run

    def start_replay(self, recording: Path, video: Path, video_name: str) -> Run:
        saved = json.loads(recording.read_text(encoding="utf-8"))
        run = self._new(video, video_name, "replay", open_pr=False)
        if saved["events"]:
            run.recorded_duration_s = round(saved["events"][-1]["ts"] - saved["events"][0]["ts"], 1)
        threading.Thread(target=self._replay, args=(run, saved), daemon=True, name=f"replay-{run.id}").start()
        return run

    def _live(self, run: Run, on_finish: Callable[[], None] | None = None) -> None:
        # Imported here so the web server starts even if an optional pipeline dependency is missing.
        from app.pipeline import run_pipeline

        bus = EventBus(run_id=run.id)
        bus.subscribe(lambda event: run.events.append(json.loads(event.model_dump_json())))
        try:
            result = run_pipeline(run.video, open_pr=run.open_pr, bus=bus)
            run.result = json.loads(result.model_dump_json())
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            (RUNS_DIR / f"{run.id}.json").write_text(json.dumps(
                {"video_name": run.video_name, "result": run.result, "events": run.events,
                 "usage": bus.tracker.summary()}, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - report any failure to the browser instead of hanging
            traceback.print_exc()
            bus.emit("pipeline", "failed", friendly_error(exc))
        finally:
            run.finished = True
            if on_finish is not None:
                on_finish()

    def _replay(self, run: Run, saved: dict) -> None:
        previous_ts = None
        for seq, event in enumerate(saved["events"]):
            if previous_ts is not None:
                time.sleep(min(max(event["ts"] - previous_ts, 0.05), REPLAY_MAX_GAP_S))
            previous_ts = event["ts"]
            run.events.append({**event, "run_id": run.id, "seq": seq, "ts": time.time()})
        run.result = saved.get("result")
        run.finished = True


async def stream(run: Run, last_id: int = -1) -> AsyncIterator[str]:
    """SSE messages for `run`, starting after event `last_id`, until the run finishes."""
    sent = last_id + 1
    last_write = time.monotonic()
    while True:
        while sent < len(run.events):
            event = PipelineEvent.model_validate(run.events[sent])
            yield event.sse()
            sent += 1
            last_write = time.monotonic()
        if run.finished and sent >= len(run.events):
            yield f"event: end\ndata: {json.dumps({'run_id': run.id})}\n\n"
            return
        if time.monotonic() - last_write > KEEPALIVE_S:
            yield ": keep-alive\n\n"  # stops proxies from closing an idle connection
            last_write = time.monotonic()
        await asyncio.sleep(0.2)


def save_upload(fileobj, suffix: str) -> Path:
    folder = UPLOADS_DIR / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"recording{suffix}"
    with path.open("wb") as fh:
        shutil.copyfileobj(fileobj, fh)
    return path


def recording_for(sample: str) -> Path | None:
    path = DEMO_DIR / f"{sample}.json"
    return path if path.exists() else None
