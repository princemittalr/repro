"""REPRODUCE stage: turn repro steps into a Playwright test that fails on the bug.

Nemotron 3 Super (the coder model) writes a Python Playwright test from the
`ReproReport` and a snapshot of the app's start page (accessibility tree, test
ids, trimmed HTML), so it can pick selectors that really exist. The test runs
in the sandbox against a fresh copy of the app, and the outcome decides what
happens next:

* **red**: the assertion about the *expected* behaviour fails on the buggy
  app. The bug is proven, and this becomes the regression test.
* **broken**: the test itself is wrong (selector not found, script error,
  timeout, unsafe code). The error and a snapshot of the page at failure go
  back to the model, which corrects the test.
* **green**: the test passed, so it did not catch the bug. The model is told
  to assert the expected behaviour that the bug breaks.

After two failed attempts the last one escalates to Nemotron 3 Ultra.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from app.config import settings
from app.llm import TruncatedReply, chat
from app.sandbox import page_snapshot, run_test
from app.schemas import ReproAttempt, ReproReport, ReproResult, ReproStatus, TestRun
from app.target import running_app
from app.usage import UsageTracker

MAX_ATTEMPTS = 3
CODER_MAX_TOKENS = 12000  # reasoning + code

# Playwright messages that mean "your selector is wrong", not "the app is wrong".
_SELECTOR_PROBLEMS = ("element(s) not found", "strict mode violation", "not attached", "Timeout")

SYSTEM_PROMPT = """You are a senior test engineer. You turn bug reports into ONE automated browser test
written in Python with Playwright's sync API.

Requirements:
- Define exactly `def test_repro(page, base_url):` at top level. `page` is a Playwright Page that is
  already open; start with `page.goto(base_url)`. Use only `from playwright.sync_api import expect`
  and, if needed, `import re`. No other imports, no files, no network calls, no sleeps.
- Replay the reported steps in order. Above each step, add a comment with the step number and the
  video timestamp, e.g. `# Step 3 (t=6.0s): apply the coupon again`.
- Pick selectors from the page snapshot only. Prefer `page.get_by_test_id(...)`, then
  `get_by_role(..., name=...)`, then `get_by_text(...)`. When the same label appears several times
  (e.g. many "Add to cart" buttons), scope it to the right card or row, or use its test id.
- Elements that only appear after an action (cart rows, messages) are not in the snapshot. Infer their
  selectors from the patterns you can see, e.g. a test id like "add-tee" suggests "remove-tee".
- Assert the EXPECTED (correct) behaviour with `expect(...)`, using exact values computed from the
  prices or inputs, so the test FAILS while the bug exists and PASSES once it is fixed. Never assert
  the buggy value. When the report states an expected value, use it as written; work out only the
  values it leaves out, counting every line the page shows (fees, shipping, taxes). Prefer literal
  expected values (e.g. "$17.49"). If you
  must read a value from the page to compute one, read it BEFORE the step that is supposed to change it.
- The test must pass on ANY reasonable fix, not just one. First think of the different ways a
  developer could fix the bug (e.g. a value stops at its minimum or maximum, the item is removed, the
  action is ignored or shows an error). Assert exact values only where all of them agree; elsewhere
  assert what every fix guarantees, e.g. that a value is never negative or that a total equals the sum
  of the rows still shown, using `not_to_have_text(re.compile(...))` or values read from the page.
  This applies to checks after intermediate steps too: if any step is part of the bug, don't assert
  values right after it that depend on how it gets fixed.
- To scope to one row or card, filter the row elements themselves, not their container, e.g.
  `page.get_by_role("listitem").filter(has_text="Item name")`, or use the row's own test id.
- Keep it short and readable: it ships in a pull request as the regression test.

Reply with the complete test in a single ```python code block and nothing else."""


def classify(run: TestRun) -> tuple[ReproStatus, str]:
    """Decide whether a test run proved the bug, missed it, or is itself broken."""
    if run.outcome == "passed":
        return "green", ("The test PASSED on the buggy app, so it does not catch the bug. Assert the expected "
                         "behaviour that the bug breaks, with exact values.")
    if run.outcome == "failed":
        if any(p in run.message for p in _SELECTOR_PROBLEMS) and "Actual value" not in run.message:
            return "broken", f"An assertion could not find its element: {run.message}"
        first_line = run.message.splitlines()[0] if run.message else "assertion failed"
        return "red", f"The bug is reproduced: {first_line}"
    labels = {"error": "The test crashed", "timeout": "The test hung", "rejected": "The test was rejected by the safety check"}
    return "broken", f"{labels[run.outcome]}: {run.message}"


def _extract_code(reply: str) -> str:
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.DOTALL)
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", reply, re.DOTALL)
    return (max(blocks, key=len) if blocks else reply).strip() + "\n"


def _page_context(snapshot: dict) -> str:
    elements = "\n".join(
        f"- <{e['tag']}> testid={e['testid']!r} id={e['id']!r} text={e['text']!r}"
        + (f" aria-label={e['label']!r}" if e.get("label") else "")
        for e in snapshot["elements"] if e.get("testid") or e["tag"] in ("button", "a", "input", "select", "textarea")
    )
    return (f"URL: {snapshot['url']}  Title: {snapshot['title']}\n\n"
            f"Accessibility tree:\n{snapshot['aria']}\n\nInteractive elements:\n{elements}\n\n"
            f"HTML (trimmed):\n{snapshot['html']}")


_UI_SUFFIXES = {".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "coverage", "test", "tests", "__tests__", "e2e"}
_ELEMENT_ATTR = re.compile(r"data-testid|aria-label")


def source_elements(app_dir: Path, limit_chars: int = 5000) -> str:
    """Element templates from the app's front-end source: lines declaring a test id or aria-label.

    The start page only shows what exists before any action. Rows, dialogs and
    buttons created later (e.g. a cart line with its own Remove and quantity
    buttons) are defined in templates, so listing those lines lets the first
    attempt pick real selectors instead of guessing.
    """
    lines: list[str] = []
    for path in sorted(app_dir.rglob("*")):
        if (path.suffix not in _UI_SUFFIXES or not path.is_file() or path.stat().st_size > 200_000
                or _SKIP_DIRS & set(path.relative_to(app_dir).parts) or ".min." in path.name):
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if _ELEMENT_ATTR.search(line) and line not in lines and len(line) < 300:
                lines.append(line)
    return "\n".join(lines)[:limit_chars]


def _first_prompt(report: ReproReport, snapshot: dict, templates: str) -> str:
    source = (f"\n\nElement templates from the app's source code (these include elements that only appear "
              f"after an action, like list rows and their buttons):\n{templates}") if templates else ""
    return (f"Bug report (from a screen recording):\n{report.model_dump_json(indent=1)}\n\n"
            f"Start page of the app under test:\n{_page_context(snapshot)}{source}\n\nWrite the test.")


def _feedback(attempt: ReproAttempt) -> str:
    parts = [f"Your test was run against the app. Result: {attempt.status.upper()}. {attempt.diagnosis}"]
    if attempt.run.page_snapshot:
        parts.append("The page when the test stopped (use these exact values and selectors; elements that "
                     f"appeared and vanished during the test are listed too):\n{attempt.run.page_snapshot}")
    parts.append("Fix the test and reply with the complete corrected test in one ```python block.")
    return "\n\n".join(parts)


def _write(model: str, stage: str, messages: list[dict], tracker: UsageTracker) -> str:
    try:
        return chat(stage=stage, model=model, messages=messages, tracker=tracker,
                    max_tokens=CODER_MAX_TOKENS, temperature=0.2)
    except TruncatedReply:
        # Spent the whole budget reasoning: ask again without the thinking phase.
        return chat(stage=f"{stage}:no-think", model=model, messages=messages, tracker=tracker,
                    max_tokens=CODER_MAX_TOKENS, temperature=0.2, thinking=False)


def reproduce(report: ReproReport, app_dir: Path, tracker: UsageTracker | None = None,
              artifacts_dir: Path | None = None) -> ReproResult:
    """Write and run tests until one fails for the right reason (red), or attempts run out."""
    tracker = tracker or UsageTracker()
    attempts: list[ReproAttempt] = []

    with tempfile.TemporaryDirectory() as tmp, running_app(app_dir) as (base_url, _):
        artifacts = artifacts_dir or Path(tmp)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _first_prompt(report, page_snapshot(base_url), source_elements(app_dir))},
        ]

        for n in range(1, MAX_ATTEMPTS + 1):
            escalate = n == MAX_ATTEMPTS and n > 1
            model = settings.models.escalate if escalate else settings.models.coder
            stage = "reproduce:escalate" if escalate else "reproduce"
            reply = _write(model, f"{stage}#{n}", messages, tracker)
            code = _extract_code(reply)

            run = run_test(code, base_url, artifacts / f"attempt-{n}")
            status, diagnosis = classify(run)
            attempt = ReproAttempt(attempt=n, model=model, code=code, run=run, status=status, diagnosis=diagnosis)
            attempts.append(attempt)
            if status == "red":
                return ReproResult(reproduced=True, test_code=code, attempts=attempts)

            messages += [{"role": "assistant", "content": f"```python\n{code}```"},
                         {"role": "user", "content": _feedback(attempt)}]

    return ReproResult(reproduced=False, attempts=attempts)


def load_report(path: Path) -> ReproReport:
    """Read a report saved by scripts/perceive.py ({"report": ...}) or a bare ReproReport JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return ReproReport.model_validate(data.get("report", data))
