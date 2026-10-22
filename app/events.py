"""Pipeline events: one record per stage update, ready to stream over SSE.

Every stage reports what it is doing through an `EventBus`. The CLI prints
the events, the eval saves them, and the web UI (Phase 4) forwards them to
the browser as Server-Sent Events, so all three see exactly the same thing.

    bus = EventBus()
    bus.subscribe(print_event)
    with bus.stage("triage", model="nvidia/Nemotron-3_5-Lightning") as stage:
        ...
        stage.done("Ranked 4 files", payload={"files": [...]})
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Literal

from pydantic import BaseModel, Field

from app.usage import UsageTracker

Stage = Literal["perceive", "triage", "reproduce", "research", "fix", "verify", "ship", "pipeline"]
Status = Literal["started", "progress", "done", "failed", "skipped"]


class PipelineEvent(BaseModel):
    run_id: str
    seq: int = Field(description="Position in the run, so clients can order and de-duplicate events")
    stage: Stage
    status: Status
    message: str
    model: str | None = Field(default=None, description="Model that handled this step, if any")
    tokens: int = Field(default=0, description="Prompt + completion tokens used by this stage so far")
    latency_s: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)

    def sse(self) -> str:
        """Encode as one Server-Sent Events message."""
        return f"id: {self.seq}\nevent: {self.stage}\ndata: {self.model_dump_json()}\n\n"


class EventBus:
    """Collects events for one pipeline run and fans them out to subscribers."""

    def __init__(self, tracker: UsageTracker | None = None, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.tracker = tracker or UsageTracker()
        self.events: list[PipelineEvent] = []
        self._subscribers: list[Callable[[PipelineEvent], None]] = []

    def subscribe(self, callback: Callable[[PipelineEvent], None]) -> None:
        self._subscribers.append(callback)

    def emit(self, stage: Stage, status: Status, message: str, **fields: Any) -> PipelineEvent:
        event = PipelineEvent(run_id=self.run_id, seq=len(self.events), stage=stage, status=status,
                              message=message, **fields)
        self.events.append(event)
        for callback in self._subscribers:
            callback(event)
        return event

    @contextmanager
    def stage(self, stage: Stage, message: str = "", model: str | None = None) -> Iterator["StageReporter"]:
        """Emit `started`, then `done` or `failed`, with the tokens and time the stage used."""
        reporter = StageReporter(self, stage, model)
        self.emit(stage, "started", message or f"{stage.capitalize()} started", model=model)
        try:
            yield reporter
        except Exception as exc:
            reporter.failed(f"{type(exc).__name__}: {exc}")
            raise
        if not reporter.finished:
            reporter.done(f"{stage.capitalize()} finished")

    def dump(self) -> list[dict]:
        return [json.loads(e.model_dump_json()) for e in self.events]


class StageReporter:
    """Handle for one running stage; measures its tokens and latency."""

    def __init__(self, bus: EventBus, stage: Stage, model: str | None) -> None:
        self.bus, self.stage, self.model = bus, stage, model
        self.finished = False
        self._start = time.perf_counter()
        self._calls_before = len(bus.tracker.calls)

    def tokens(self) -> int:
        calls = self.bus.tracker.calls[self._calls_before:]
        return sum(c.prompt_tokens + c.completion_tokens for c in calls)

    def _emit(self, status: Status, message: str, model: str | None, payload: dict | None) -> PipelineEvent:
        return self.bus.emit(self.stage, status, message, model=model or self.model, tokens=self.tokens(),
                             latency_s=round(time.perf_counter() - self._start, 2), payload=payload or {})

    def progress(self, message: str, model: str | None = None, payload: dict | None = None) -> PipelineEvent:
        return self._emit("progress", message, model, payload)

    def done(self, message: str, model: str | None = None, payload: dict | None = None) -> PipelineEvent:
        self.finished = True
        return self._emit("done", message, model, payload)

    def failed(self, message: str, model: str | None = None, payload: dict | None = None) -> PipelineEvent:
        self.finished = True
        return self._emit("failed", message, model, payload)

    def skipped(self, message: str, payload: dict | None = None) -> PipelineEvent:
        self.finished = True
        return self._emit("skipped", message, None, payload)
