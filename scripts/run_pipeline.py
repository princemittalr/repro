"""Run the whole Repro pipeline from the command line.

Usage:
    python -m scripts.run_pipeline samples/shoplite-coupon.mp4             # clones GITHUB_DEMO_REPO, opens a PR
    python -m scripts.run_pipeline samples/shoplite-remove.mp4 --no-pr     # everything except the PR
    python -m scripts.run_pipeline samples/shoplite-remove.mp4 --app shoplite --no-pr   # local folder, offline repo
    python -m scripts.run_pipeline samples/shoplite-coupon.mp4 --report runs/eval_reproduce/coupon.json

Every stage event is printed as it happens, and the full run (events, report,
test, diff, PR) is saved to runs/pipeline/<run_id>.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.config import PROJECT_ROOT
from app.events import EventBus, PipelineEvent
from app.llm import friendly_error
from app.pipeline import run_pipeline
from app.reproduce import load_report

ICONS = {"started": ">>", "progress": "  ", "done": "OK", "failed": "!!", "skipped": "--"}


def print_event(event: PipelineEvent) -> None:
    model = f" [{event.model.split('/')[-1]}]" if event.model else ""
    tokens = f"  ({event.tokens:,} tok, {event.latency_s:.1f}s)" if event.status in ("done", "failed") else ""
    print(f"{ICONS[event.status]} {event.stage:<9}{model} {event.message[:160]}{tokens}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Video in, verified fix and pull request out.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--repo", help="GitHub repo to fix (owner/name); default: GITHUB_DEMO_REPO")
    parser.add_argument("--app", type=Path, help="fix a local app folder instead of cloning a repo")
    parser.add_argument("--no-pr", action="store_true", help="stop after VERIFY, don't open a pull request")
    parser.add_argument("--report", type=Path, help="reuse a saved perception report instead of watching the video")
    args = parser.parse_args()

    bus = EventBus()
    bus.subscribe(print_event)
    start = time.perf_counter()
    try:
        result = run_pipeline(args.video, repo=args.repo, app_dir=args.app, open_pr=not args.no_pr,
                              report=load_report(args.report) if args.report else None, bus=bus)
    except Exception as exc:  # noqa: BLE001 - the failed stage was already reported as an event
        print(f"\nFAILED: {friendly_error(exc)}")
        raise SystemExit(1) from None

    out = PROJECT_ROOT / "runs" / "pipeline" / f"{bus.run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"result": result.model_dump(), "events": bus.dump(),
                               "usage": bus.tracker.summary()}, indent=2, default=str), encoding="utf-8")
    usage = bus.tracker.summary()
    print(f"\n{'SUCCESS' if result.success else 'FAILED'} in {time.perf_counter() - start:.0f}s, "
          f"{usage['prompt_tokens'] + usage['completion_tokens']:,} tokens. Saved {out.relative_to(PROJECT_ROOT)}")
    if result.pull_request:
        print(f"Pull request: {result.pull_request.url}")
    raise SystemExit(0 if result.success else 1)


if __name__ == "__main__":
    main()
