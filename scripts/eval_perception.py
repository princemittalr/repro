"""Score the PERCEIVE stage against ground truth for the ShopLite samples.

Usage:
    python -m scripts.eval_perception                      # all samples, default mode
    python -m scripts.eval_perception --modes video hybrid frames
    python -m scripts.eval_perception --runs 3             # repeat to measure consistency
    python -m scripts.eval_perception --verify             # add the text-only review pass

For each recording it checks:
  * step recall: every real click/type is in the report, with the right target,
    and its timestamp is within 1.5 s of the truth;
  * facts: key on-screen values are quoted and known hallucinations are absent.

The draft and the final (verified) report are both scored, and everything is
saved to runs/eval/<mode>-<sample>-<run>.json so failures can be inspected.
"""

from __future__ import annotations

import argparse
import json
from statistics import mean

from app.config import PROJECT_ROOT
from app.perception import perceive
from app.schemas import ReproReport
from app.usage import UsageTracker

SAMPLES = PROJECT_ROOT / "samples"
OUT_DIR = PROJECT_ROOT / "runs" / "eval"
TIME_TOLERANCE_S = 1.5


def score(report: ReproReport, scenario: str, truth: dict) -> dict:
    events = json.loads((SAMPLES / f"shoplite-{scenario}.events.json").read_text(encoding="utf-8"))["events"]
    steps = [s for s in report.steps if s.action in ("click", "type")]
    used: set[int] = set()
    matched, time_errors = 0, []

    for event in events:
        keywords = truth["target_keywords"][event["test_id"]]
        for i, step in enumerate(steps):
            text = f"{step.target} {step.value or ''}".lower()
            if i in used or step.action != event["action"] or not all(k in text for k in keywords):
                continue
            if abs(step.timestamp_s - event["timestamp_s"]) <= TIME_TOLERANCE_S:
                used.add(i)
                matched += 1
                time_errors.append(abs(step.timestamp_s - event["timestamp_s"]))
                break

    blob = report.model_dump_json().lower()
    facts = truth["facts"][scenario]
    missing = [f for f in facts["must_mention"] if f.lower() not in blob]
    hallucinated = [f for f in facts["must_not_mention"] if f.lower() in blob]

    return {
        "recall": matched / len(events),
        "extra_steps": len(steps) - matched,
        "time_err": mean(time_errors) if time_errors else None,
        "facts_ok": not missing and not hallucinated,
        "problems": [f"missing '{m}'" for m in missing] + [f"hallucinated '{h}'" for h in hallucinated],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", nargs="+", default=["hybrid"], choices=["video", "frames", "hybrid"])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--verify", action="store_true", help="add the text-only self-verification pass")
    args = parser.parse_args()

    truth = json.loads((SAMPLES / "ground_truth.json").read_text(encoding="utf-8"))
    print(f"{'mode':<7} {'sample':<9} {'recall':>6} {'extra':>5} {'t-err':>6} {'facts':>5}  tokens  notes")

    for mode in args.modes:
        totals = []
        for scenario in truth["facts"]:
            for run in range(1, args.runs + 1):
                tracker, trace = UsageTracker(), {}
                try:
                    report = perceive(SAMPLES / f"shoplite-{scenario}.mp4", mode=mode, tracker=tracker,
                                      verify=args.verify, trace=trace)
                except Exception as exc:  # noqa: BLE001 - report and keep scoring the rest
                    print(f"{mode:<7} {scenario:<9} ERROR {type(exc).__name__}: {str(exc)[:150]}")
                    continue
                r = score(report, scenario, truth)
                totals.append(r)
                usage = tracker.summary()
                tokens = usage["prompt_tokens"] + usage["completion_tokens"]
                draft = trace.get("draft")
                d = score(draft, scenario, truth) if draft else None
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                (OUT_DIR / f"{mode}-{scenario}-{run}.json").write_text(json.dumps({
                    "score": r, "draft_score": d, "usage": usage,
                    "report": report.model_dump(), "draft": draft.model_dump() if draft else None,
                    "trace": {k: v for k, v in trace.items() if k != "draft"},
                }, indent=2, default=str), encoding="utf-8")
                t_err = f"{r['time_err']:.1f}s" if r["time_err"] is not None else "-"
                print(f"{mode:<7} {scenario:<9} {r['recall']:>6.0%} {r['extra_steps']:>5} {t_err:>6} "
                      f"{'yes' if r['facts_ok'] else 'NO':>5}  {tokens:>6}  {'; '.join(r['problems'])}"
                      + (f"  [draft {d['recall']:.0%}, facts {'yes' if d['facts_ok'] else 'NO'}]" if d else ""))
        if totals:
            print(f"{mode:<7} {'OVERALL':<9} {mean(r['recall'] for r in totals):>6.0%} "
                  f"{'':>5} {'':>6} {sum(r['facts_ok'] for r in totals)}/{len(totals)}\n")


if __name__ == "__main__":
    main()
