"""Run the PERCEIVE stage on a video from the command line.

Usage:
    python -m scripts.perceive samples/shoplite-coupon.mp4
    python -m scripts.perceive my_recording.mp4 --mode frames
    python -m scripts.perceive samples/shoplite-coupon.mp4 --mode all    # compare strategies

The report is printed and saved to runs/<video-name>.<mode>.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.config import PROJECT_ROOT
from app.perception import perceive
from app.usage import UsageTracker


def run(video: Path, mode: str, verify: bool = False) -> None:
    tracker = UsageTracker()
    start = time.perf_counter()
    report = perceive(video, mode=mode, tracker=tracker, verify=verify)
    elapsed = time.perf_counter() - start

    print(f"\n=== {video.name} | mode={mode} | {elapsed:.1f}s ===")
    print(f"Title:    {report.title}")
    print(f"Page:     {report.page}")
    for step in report.steps:
        value = f' = "{step.value}"' if step.value else ""
        print(f"  {step.index:>2}. [{step.timestamp_s:5.1f}s] {step.action:<8} {step.target}{value}")
    print(f"Expected: {report.expected_behavior}")
    print(f"Actual:   {report.actual_behavior}")
    for text in report.error_text:
        print(f"Evidence: {text}")
    print(f"Confidence: {report.confidence:.2f}")

    usage = tracker.summary()
    for call in usage["calls"]:
        print(f"Model call: {call['stage']} -> {call['model']} "
              f"({call['prompt_tokens']} in / {call['completion_tokens']} out, {call['latency_s']}s)")

    out_dir = PROJECT_ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{video.stem}.{mode}.json"
    out.write_text(json.dumps({"report": report.model_dump(), "usage": usage}, indent=2), encoding="utf-8")
    print(f"Saved {out.relative_to(PROJECT_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn a bug screen recording into repro steps.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--mode", choices=["hybrid", "video", "frames", "all"], default="hybrid")
    parser.add_argument("--verify", action="store_true", help="add the text-only self-verification pass")
    args = parser.parse_args()

    if not args.video.exists():
        raise SystemExit(f"File not found: {args.video}")
    modes = ["hybrid", "video", "frames"] if args.mode == "all" else [args.mode]
    for mode in modes:
        try:
            run(args.video, mode, verify=args.verify)
        except Exception as exc:  # noqa: BLE001 - in "all" mode, report and try the next strategy
            if len(modes) == 1:
                raise
            print(f"\n=== {args.video.name} | mode={mode} | FAILED ===\n{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
