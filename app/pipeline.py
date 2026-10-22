"""The whole Repro pipeline: video -> perceive -> triage -> reproduce -> research/fix -> verify -> PR.

Each stage reports through an `EventBus` (see app/events.py), so the same run
can be watched from the CLI, saved by the eval, or streamed to the web UI.

Routing (which Nemotron model does what):
    perceive   Nemotron 3.5 Super VL   watches the recording
    triage     Nemotron 3.5 Lightning  ranks the files to read first (cheap)
    reproduce  Nemotron 3 Super        writes the failing Playwright test
    fix        Nemotron 3 Super        tool-using agent, with Tavily research
    escalate   Nemotron 3 Ultra        takes over when Super fails twice
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel

from app.checks import Checks, run_checks
from app.config import settings
from app.events import EventBus
from app.fix import FixResult, FixStep, fix
from app.perception import perceive
from app.reproduce import reproduce
from app.research import ResearchEntry
from app.schemas import ReproReport, ReproResult
from app.ship import PullRequestInfo, clone, e2e_test_file, open_pull_request
from app.target import IGNORED
from app.triage import Triage, triage


class PipelineResult(BaseModel):
    run_id: str
    report: ReproReport | None = None
    triage: Triage | None = None
    repro: ReproResult | None = None
    fix: FixResult | None = None
    verify: Checks | None = None
    pull_request: PullRequestInfo | None = None
    success: bool = False


def _fmt_time(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def _models_table(result: PipelineResult, bus: EventBus) -> str:
    rows = []
    for stage in ("perceive", "triage", "reproduce", "research", "fix", "verify"):
        done = [e for e in bus.events if e.stage == stage and e.status in ("done", "failed", "skipped")]
        if not done:
            continue
        models = sorted({e.model for e in bus.events if e.stage == stage and e.model})
        if stage == "research":
            models = ["Tavily search API"]
        rows.append(f"| {stage.capitalize()} | {', '.join(m.split('/')[-1] for m in models) or '-'} | "
                    f"{done[-1].tokens:,} | {done[-1].latency_s:.0f}s |")
    return "| Stage | Model | Tokens | Time |\n|---|---|---|---|\n" + "\n".join(rows)


def pr_body(result: PipelineResult, bus: EventBus, video_name: str, red_message: str) -> str:
    report, fixed, checks = result.report, result.fix, result.verify
    steps = "\n".join(
        f"| {s.index} | {_fmt_time(s.timestamp_s)} | {s.action} | {s.target} | {s.value or ''} |"
        for s in report.steps)
    research_lines = "\n".join(
        f"- `{e.query}`" + "".join(f"\n  - [{s.title or s.url}]({s.url})" for s in e.sources[:3])
        for e in fixed.research) or "- Not needed for this bug."
    suite = checks.suite
    suite_line = (f"existing suite (`{suite.command}`) passed" if suite.command and suite.passed
                  else "no existing suite found" if not suite.command else "existing suite FAILED")
    files = "\n".join(f"- `{f}`" for f in fixed.changed_files)
    escalation = " Nemotron 3 Ultra took over after Super's attempts failed." if fixed.escalated else ""
    return f"""## {report.title}

Repro watched a screen recording of this bug, reproduced it with a failing Playwright test, fixed the
code and verified the fix, without a human writing any of it.

### Steps to reproduce (timestamps in `{video_name}`)

| # | Time | Action | Target | Value |
|---|------|--------|--------|-------|
{steps}

**Expected:** {report.expected_behavior}

**Actual:** {report.actual_behavior}

### Evidence

- **Red, before the fix:** the new regression test failed: `{red_message.strip()[:300]}`
- **Green, after the fix:** the regression test passed, and the {suite_line}.

### Root cause

{fixed.root_cause}

### The fix

{fixed.summary}

Files changed:
{files}

### Research (Tavily)

{research_lines}

### How Repro handled it

{_models_table(result, bus)}
{escalation}

<sub>Opened by Repro (Nebius Token Factory + NVIDIA Nemotron). Run `{result.run_id}`.</sub>
"""


def run_pipeline(
    video: Path,
    *,
    repo: str | None = None,
    app_dir: Path | None = None,
    open_pr: bool = True,
    report: ReproReport | None = None,
    bus: EventBus | None = None,
) -> PipelineResult:
    """Run every stage on `video` against `repo` (cloned fresh) or a local `app_dir`."""
    bus = bus or EventBus()
    tracker = bus.tracker
    result = PipelineResult(run_id=bus.run_id)
    repo = repo or (settings.github_demo_repo if app_dir is None else None)
    bus.emit("pipeline", "started", f"Repro run on {video.name}",
             payload={"video": video.name, "repo": repo, "app_dir": str(app_dir) if app_dir else None})

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        if repo:
            source = clone(repo, tmp_dir / "source")
        else:
            source = tmp_dir / "source"
            shutil.copytree(app_dir, source, ignore=IGNORED)

        # PERCEIVE
        with bus.stage("perceive", "Watching the recording", model=settings.models.perceive) as st:
            if report is None:
                report = perceive(video, tracker=tracker)
            result.report = report
            st.done(f"{len(report.steps)} steps: {report.title}", payload={"report": report.model_dump()})

        # TRIAGE
        with bus.stage("triage", "Ranking likely files", model=settings.models.triage) as st:
            result.triage = triage(report, source, tracker=tracker)
            st.done(f"Start with {', '.join(f.path for f in result.triage.files) or 'a full search'}",
                    payload=result.triage.model_dump())

        # REPRODUCE
        with bus.stage("reproduce", "Writing a failing Playwright test", model=settings.models.coder) as st:
            result.repro = reproduce(report, source, tracker=tracker)
            attempts = [{"attempt": a.attempt, "model": a.model, "status": a.status, "diagnosis": a.diagnosis}
                        for a in result.repro.attempts]
            payload = {"attempts": attempts, "test_code": result.repro.test_code}
            if not result.repro.reproduced:
                st.failed("Could not write a test that fails on the bug", payload=payload)
                bus.emit("pipeline", "failed", "Stopped: the bug could not be reproduced")
                return result
            red = result.repro.attempts[-1]
            st.done(f"RED in {len(attempts)} attempt(s): {red.diagnosis[:160]}", model=red.model, payload=payload)
        red_message = red.run.message.splitlines()[0] if red.run.message else red.diagnosis

        # RESEARCH + FIX (research happens inside the fix loop, whenever the agent asks)
        work = tmp_dir / "work"
        shutil.copytree(source, work, ignore=IGNORED)
        bus.emit("research", "started", "Web research is available to the fix agent (Tavily)")
        with bus.stage("fix", "Fixing the code", model=settings.models.coder) as st:
            def on_step(step: FixStep) -> None:
                st.progress(f"{step.tool}({', '.join(f'{k}={str(v)[:60]}' for k, v in step.args.items())})",
                            model=step.model, payload=step.model_dump())

            def on_research(entry: ResearchEntry) -> None:
                bus.emit("research", "progress", f"Searched: {entry.query}", payload=entry.model_dump())

            def on_escalate(reason: str) -> None:
                st.progress(f"Escalating to {settings.models.escalate.split('/')[-1]}: {reason}",
                            model=settings.models.escalate, payload={"escalated": True, "reason": reason})

            result.fix = fix(report, result.repro.test_code, red_message, work, result.triage, tracker=tracker,
                             on_step=on_step, on_research=on_research, on_escalate=on_escalate)
            payload = result.fix.model_dump(exclude={"steps", "checks"})
            if result.fix.fixed:
                st.done(f"Fixed {', '.join(result.fix.changed_files)}: {result.fix.summary[:160]}", payload=payload)
            else:
                st.failed("The fix agent did not reach a green run", payload=payload)
        searches = len(result.fix.research)
        if searches:
            bus.emit("research", "done", f"{searches} search(es) used", payload={"count": searches})
        else:
            bus.emit("research", "skipped", "The agent did not need outside knowledge for this bug")
        if not result.fix.fixed:
            bus.emit("pipeline", "failed", "Stopped: no verified fix")
            return result

        # VERIFY: same checks once more, on the final code, outside the agent's loop.
        with bus.stage("verify", "Re-running the regression test and the existing suite") as st:
            result.verify = run_checks(work, result.repro.test_code)
            payload = {"summary": result.verify.summary(), "green": result.verify.green, "diff": result.fix.diff}
            if not result.verify.green:
                st.failed("Verification failed", payload=payload)
                bus.emit("pipeline", "failed", "Stopped: the fix did not verify")
                return result
            st.done("GREEN: regression test and existing suite pass", payload=payload)

        # SHIP
        if not (open_pr and repo):
            bus.emit("ship", "skipped", "Pull request not requested")
        else:
            with bus.stage("ship", f"Opening a pull request on {repo}") as st:
                files = dict(result.fix.files)
                test_path, test_content = e2e_test_file(result.repro.test_code, report.title, video.name)
                files[test_path] = test_content
                body = pr_body(result, bus, video.name, red_message)
                result.pull_request = open_pull_request(repo, files, report.title, body, branch_hint=report.title)
                st.done(f"PR #{result.pull_request.number} opened", payload=result.pull_request.model_dump())

    result.success = True
    usage = tracker.summary()
    bus.emit("pipeline", "done", "Bug fixed and verified" + (" - PR opened" if result.pull_request else ""),
             tokens=usage["prompt_tokens"] + usage["completion_tokens"],
             payload={"pr_url": result.pull_request.url if result.pull_request else None,
                      "prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"],
                      "cost_usd": usage["cost_usd"]})
    return result
