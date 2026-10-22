"""Run the REPRODUCE stage from the command line.

Usage:
    python -m scripts.reproduce runs/shoplite-coupon.hybrid.json      # from a saved perceive report
    python -m scripts.reproduce samples/shoplite-coupon.mp4           # perceive first, then reproduce
    python -m scripts.reproduce samples/shoplite-coupon.mp4 --app path/to/app

Prints every attempt (model, outcome, diagnosis) and saves the red test to
runs/<name>.repro_test.py.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from app.config import PROJECT_ROOT
from app.perception import perceive
from app.reproduce import load_report, reproduce
from app.usage import UsageTracker

VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a failing Playwright test that proves the bug.")
    parser.add_argument("source", type=Path, help="a video, or a report JSON saved by scripts.perceive")
    parser.add_argument("--app", type=Path, default=PROJECT_ROOT / "shoplite", help="folder of the app under test")
    args = parser.parse_args()

    tracker = UsageTracker()
    start = time.perf_counter()
    if args.source.suffix.lower() in VIDEO_SUFFIXES:
        print(f"Perceiving {args.source.name} ...")
        report = perceive(args.source, tracker=tracker)
    else:
        report = load_report(args.source)
    print(f"Bug: {report.title}\n")

    result = reproduce(report, args.app, tracker=tracker, artifacts_dir=PROJECT_ROOT / "runs" / "artifacts")
    for a in result.attempts:
        print(f"Attempt {a.attempt} [{a.model}] -> {a.status.upper()} ({a.run.outcome}, {a.run.duration_s}s)")
        print(f"  {a.diagnosis.splitlines()[0][:200]}")

    usage = tracker.summary()
    print(f"\nModel calls: {len(usage['calls'])}, tokens: {usage['prompt_tokens']} in / "
          f"{usage['completion_tokens']} out, time: {time.perf_counter() - start:.0f}s")

    if result.reproduced:
        name = args.source.name.split(".")[0]
        out = PROJECT_ROOT / "runs" / f"{name}.repro_test.py"
        out.parent.mkdir(exist_ok=True)
        out.write_text(result.test_code, encoding="utf-8")
        print(f"RED: bug reproduced. Test saved to {out.relative_to(PROJECT_ROOT)}")
    else:
        print("Could not reproduce the bug within the attempt limit.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
