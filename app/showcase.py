"""Real run data for the landing page: the ticker, the run pings and the proof cards.

Everything here comes from runs that actually happened: the recorded demo runs
in demo/ (the same files Replay plays back) and live runs this server finished
(runs/pipeline/). Nothing is invented. Uploaded recordings can contain anything,
so for live runs only the sample name (or "an uploaded recording"), the outcome
and the timings are shown, never text the models derived from the video.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings
from app.runs import DEMO_DIR, RUNS_DIR

MAX_LIVE_RUNS = 20


def _tests(verify: dict | None) -> tuple[int, int] | None:
    """(passed, total) from the existing suite's `node --test` output, if it reported counts."""
    output = ((verify or {}).get("suite") or {}).get("output") or ""
    total, passed = re.search(r"tests (\d+)", output), re.search(r"pass (\d+)", output)
    return (int(passed.group(1)), int(total.group(1))) if total and passed else None


def _pull_request(result: dict, source: str) -> dict | None:
    """The PR link. Recorded runs ship in this public repo; a live run's PR is shown only when it
    went to the public demo repo."""
    pr = result.get("pull_request") or {}
    repo = settings.github_demo_repo
    public = source == "recorded" or (repo and str(pr.get("url", "")).startswith(f"https://github.com/{repo}/pull/"))
    return {"number": pr["number"], "url": pr["url"]} if pr.get("url") and public else None


def summarize(saved: dict, source: str, samples: dict[str, tuple[str, str]], sample: str | None = None) -> dict | None:
    """One public, privacy-safe line about a saved run (demo/<sample>.json or runs/pipeline/*.json)."""
    events, result = saved.get("events") or [], saved.get("result") or {}
    if not events or not result:
        return None
    if sample is None:
        match = re.fullmatch(r"shoplite-(\w+)\.\w+", saved.get("video_name") or "")
        sample = match.group(1) if match else None
    sample = sample if sample in samples else None
    report, fix = result.get("report") or {}, result.get("fix") or {}
    detail = source == "recorded"  # live runs show the outcome only, never text derived from the video
    usage = saved.get("usage") or {}
    tests = _tests(result.get("verify"))
    return {
        "source": source,                                 # "recorded" (ships with the repo) or "live"
        "sample": sample,
        "title": samples[sample][0] if sample else "An uploaded recording",
        "bug": report.get("title") if detail else None,
        "success": bool(result.get("success")),
        "fixed": bool(fix.get("fixed")),
        "root_cause": fix.get("root_cause") if detail else None,
        "summary": fix.get("summary") if detail else None,
        "changed_files": fix.get("changed_files") if detail else None,
        "escalated": bool(fix.get("escalated")),
        "confidence": report.get("confidence") if detail else None,
        "steps": len(report.get("steps") or []) if detail else None,
        "duration_s": round(events[-1]["ts"] - events[0]["ts"], 1),
        "tokens": (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
        "tests_passed": tests[0] if tests else None,
        "tests_total": tests[1] if tests else None,
        "pull_request": _pull_request(result, source),
        "started": events[0]["ts"],
    }


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recent_runs(samples: dict[str, tuple[str, str]]) -> list[dict]:
    """Live runs finished on this server (newest first), then the recorded demo runs."""
    live = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_LIVE_RUNS] \
        if RUNS_DIR.exists() else []
    out = []
    for path in live:
        if (saved := _load(path)) and (line := summarize(saved, "live", samples)):
            out.append(line)
    for path in sorted(DEMO_DIR.glob("*.json")):
        if (saved := _load(path)) and (line := summarize(saved, "recorded", samples, sample=path.stem)):
            out.append(line)
    return out
