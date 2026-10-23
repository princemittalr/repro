"""End-to-end eval of the full pipeline on the ShopLite samples.

Usage:
    python -m scripts.eval_pipeline                         # coupon + remove, no PRs
    python -m scripts.eval_pipeline --samples remove --reuse-reports
    python -m scripts.eval_pipeline --samples coupon --pr   # also open (and keep) a real PR

For each sample: perceive -> triage -> reproduce -> fix -> verify. It then
checks that the agent's fix BEHAVES like the reference fix in
samples/reference/cart.fixed.js: the same cart scenario is run through Node
on both versions of cart.js and the totals must match. The existing suite
must pass too (VERIFY already enforces that). Results go to runs/eval_pipeline/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import PROJECT_ROOT
from app.events import EventBus
from app.pipeline import run_pipeline
from app.schemas import ReproReport

SAMPLES = PROJECT_ROOT / "samples"
SHOPLITE = PROJECT_ROOT / "shoplite"
OUT = PROJECT_ROOT / "runs" / "eval_pipeline"

# One cart scenario per bug, written against ShopLite's cart.js API. Eval-only: the agent never sees these.
SCENARIOS = {
    "coupon": 'addItem(c, "tee"); addItem(c, "mug"); applyCoupon(c, "SAVE10"); applyCoupon(c, "SAVE10");',
    "remove": 'addItem(c, "cap"); addItem(c, "stickers"); addItem(c, "mug"); removeItem(c, "cap");',
    "quantity": 'addItem(c, "tee"); for (let i = 0; i < 3; i++) setQuantity(c, "tee", c.items[0] ? c.items[0].quantity - 1 : 0);',
}


def scenario_totals(cart_js: Path, scenario: str) -> dict:
    script = (f"const m = await import({json.dumps(cart_js.resolve().as_uri())});"
              "const { addItem, applyCoupon, createCart, removeItem, setQuantity, totals } = m;"
              f"const c = createCart(); {SCENARIOS[scenario]} console.log(JSON.stringify(totals(c)));")
    proc = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-300:])
    return json.loads(proc.stdout)


def behaves_like_reference(files: dict[str, str], scenario: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "app"
        shutil.copytree(SHOPLITE, app, ignore=shutil.ignore_patterns("node_modules", ".git"))
        for path, content in files.items():
            (app / path).parent.mkdir(parents=True, exist_ok=True)
            (app / path).write_text(content, encoding="utf-8")
        got = scenario_totals(app / "public" / "cart.js", scenario)
    want = scenario_totals(SAMPLES / "reference" / "cart.fixed.js", scenario)
    return got == want, f"got {got}, reference {want}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", nargs="+", default=["coupon", "remove"], choices=list(SCENARIOS))
    parser.add_argument("--reuse-reports", action="store_true", help="reuse runs/eval_reproduce/<sample>.json reports")
    parser.add_argument("--pr", action="store_true", help="open a real pull request for each sample")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print(f"{'sample':<9} {'red':>4} {'fixed':>5} {'verify':>6} {'like-ref':>8} {'escal.':>6} {'search':>6} "
          f"{'tokens':>7}  notes")
    for name in args.samples:
        saved = PROJECT_ROOT / "runs" / "eval_reproduce" / f"{name}.json"
        report = (ReproReport.model_validate(json.loads(saved.read_text(encoding="utf-8"))["report"])
                  if args.reuse_reports and saved.exists() else None)
        bus = EventBus()
        try:
            result = run_pipeline(SAMPLES / f"shoplite-{name}.mp4", app_dir=None if args.pr else SHOPLITE,
                                  open_pr=args.pr, report=report, bus=bus)
        except Exception as exc:  # noqa: BLE001 - keep evaluating the other samples
            print(f"{name:<9} ERROR {type(exc).__name__}: {str(exc)[:200]}")
            continue

        like_ref, note = (False, "no fix")
        if result.fix and result.fix.fixed:
            like_ref, note = behaves_like_reference(result.fix.files, name)
        usage = bus.tracker.summary()
        tokens = usage["prompt_tokens"] + usage["completion_tokens"]
        yes = lambda flag: "yes" if flag else "NO"  # noqa: E731
        print(f"{name:<9} {yes(result.repro and result.repro.reproduced):>4} {yes(result.fix and result.fix.fixed):>5} "
              f"{yes(result.verify and result.verify.green):>6} {yes(like_ref):>8} "
              f"{yes(result.fix and result.fix.escalated):>6} {len(result.fix.research) if result.fix else 0:>6} "
              f"{tokens:>7}  {'' if like_ref else note[:120]}"
              + (f"  PR {result.pull_request.url}" if result.pull_request else ""))
        (OUT / f"{name}.json").write_text(json.dumps({"result": result.model_dump(), "events": bus.dump(),
                                                      "usage": usage, "like_reference": like_ref, "note": note},
                                                     indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
