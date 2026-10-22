"""End-to-end check of PERCEIVE + REPRODUCE on the ShopLite samples.

Usage:
    python -m scripts.eval_reproduce
    python -m scripts.eval_reproduce --samples coupon remove

For each sample it records whether a red test was produced, in how many
attempts, and (the real proof) whether that same test PASSES once the bug is
fixed, using the reference fix in samples/reference/cart.fixed.js. A test that
is red on the buggy app and green on the fixed one pins down exactly this bug.
Tests and results are saved to runs/eval_reproduce/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from app.config import PROJECT_ROOT
from app.perception import perceive
from app.reproduce import reproduce
from app.sandbox import run_test
from app.target import IGNORED, running_app
from app.usage import UsageTracker

SAMPLES = PROJECT_ROOT / "samples"
SHOPLITE = PROJECT_ROOT / "shoplite"
OUT = PROJECT_ROOT / "runs" / "eval_reproduce"


def passes_on_fixed_app(code: str, artifacts: Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        fixed = Path(tmp) / "shoplite-fixed"
        shutil.copytree(SHOPLITE, fixed, ignore=IGNORED)
        shutil.copy(SAMPLES / "reference" / "cart.fixed.js", fixed / "public" / "cart.js")
        with running_app(fixed) as (base_url, _):
            run = run_test(code, base_url, artifacts)
    return run.outcome == "passed", run.message.splitlines()[0] if run.message else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", nargs="+", default=["coupon", "remove", "quantity"])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print(f"{'sample':<9} {'red':>4} {'tries':>5} {'fixed=green':>11} {'tokens':>7}  models / notes")
    for name in args.samples:
        tracker = UsageTracker()
        try:
            report = perceive(SAMPLES / f"shoplite-{name}.mp4", tracker=tracker)
            result = reproduce(report, SHOPLITE, tracker=tracker, artifacts_dir=OUT / name)
        except Exception as exc:  # noqa: BLE001 - keep evaluating the other samples
            print(f"{name:<9} ERROR {type(exc).__name__}: {str(exc)[:200]}")
            continue

        green, note = (False, "no red test")
        if result.reproduced:
            (OUT / f"{name}.repro_test.py").write_text(result.test_code, encoding="utf-8")
            green, note = passes_on_fixed_app(result.test_code, OUT / name / "fixed")
        (OUT / f"{name}.json").write_text(json.dumps({
            "report": report.model_dump(), "result": result.model_dump(), "usage": tracker.summary(),
        }, indent=2), encoding="utf-8")

        usage = tracker.summary()
        tokens = usage["prompt_tokens"] + usage["completion_tokens"]
        models = " > ".join(f"{a.model.split('/')[-1]}:{a.status}" for a in result.attempts)
        print(f"{name:<9} {'yes' if result.reproduced else 'NO':>4} {len(result.attempts):>5} "
              f"{'yes' if green else 'NO':>11} {tokens:>7}  {models}  {'' if green else note[:120]}")


if __name__ == "__main__":
    main()
