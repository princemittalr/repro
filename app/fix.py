"""FIX: a tool-using agent that patches the app until the regression test is green.

Nemotron 3 Super drives the loop with plain function calling. Its tools:

* ``list_files`` / ``read_file`` / ``search_code``: explore the code base;
* ``edit_file`` (exact search-and-replace) / ``write_file`` (new files only):
  change it;
* ``run_tests``: start the patched app and run the red regression test from
  REPRODUCE plus the app's own test suite;
* ``web_search`` / ``read_url``: live research with Tavily, for unfamiliar
  errors or library behaviour;
* ``finish``: accepted only when the last ``run_tests`` was all green and
  nothing changed since.

The agent works on a throwaway copy of the app. It may add new tests but
never edit existing ones, so it cannot "fix" the bug by weakening the suite.
If its changes fail the tests twice, or it runs long without a green run,
Nemotron 3 Ultra takes over the same conversation (ESCALATE). Every step
records which model made it, for the UI and the pull request.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from app import research as web
from app.checks import Checks, run_checks
from app.config import settings
from app.llm import TruncatedReply, chat_tools
from app.schemas import ReproReport
from app.triage import SKIP_DIRS, Triage
from app.usage import UsageTracker

MAX_STEPS = 24
ESCALATE_AFTER_FAILED_RUNS = 2
ESCALATE_AFTER_STEPS = 12      # ...or this many steps without a green run
TOOL_RESULT_CHARS = 6000
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.[a-z]+$")


class FixStep(BaseModel):
    n: int
    model: str
    tool: str
    args: dict = Field(default_factory=dict)
    result: str = Field(description="Shortened tool result")
    ok: bool = True


class FixResult(BaseModel):
    fixed: bool
    summary: str = ""
    root_cause: str = ""
    escalated: bool = False
    steps: list[FixStep] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict, description="New content of every changed file")
    diff: str = ""
    research: list[web.ResearchEntry] = Field(default_factory=list)
    checks: Checks | None = None


SYSTEM_PROMPT = """You are a senior software engineer fixing a bug in a web app. A screen recording of the bug was
turned into a bug report and a regression test that currently FAILS (red) because of the bug.

Your job: find the root cause and change the app's code so the regression test passes and the app's
existing tests still pass. Work like this:
1. Read the files triage suggests (and anything else you need) with read_file / search_code.
2. Explain the root cause to yourself, then make the smallest correct change with edit_file.
3. Call run_tests. If anything fails, read the output, adjust, and run again.
4. When run_tests is fully green, call finish with the root cause and a summary of the fix.

Rules:
- Fix the root cause in the application logic. Never special-case the test's data, never weaken or
  edit existing tests, never edit the regression test (you cannot see its file; it runs from outside).
- Keep the change minimal and in the style of the surrounding code, with a short comment if the
  reason is not obvious.
- If the project has a unit test suite, add ONE focused unit test for this bug in a NEW file next to
  the existing tests, in the same style (write_file).
- Use web_search when outside knowledge would help: an unfamiliar error message, how a library or
  browser API behaves, or the standard way to handle this kind of bug. Use precise queries.
- Paths are relative to the app's root folder."""

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List files in the app (relative paths, with line counts).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Folder to list; '.' for the whole app"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file with line numbers.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_code", "description": "Search all source files with a regular expression (case-insensitive).",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace one exact occurrence of old_text with new_text in a file. Include enough "
                       "surrounding lines in old_text to make it unique; whitespace must match exactly.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
            "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create a NEW file (e.g. a unit test). Existing files must be changed with edit_file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_tests",
        "description": "Start the app with your changes, run the regression test, then the existing test suite.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "web_search", "description": "Search the web (Tavily) and get a short answer plus sources.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_url", "description": "Read the text of one page returned by web_search.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "Finish once run_tests is fully green.",
        "parameters": {"type": "object", "properties": {
            "root_cause": {"type": "string", "description": "One or two sentences"},
            "summary": {"type": "string", "description": "What you changed and why"}},
            "required": ["root_cause", "summary"]}}},
]


class ToolError(ValueError):
    """A tool call the agent should correct (bad path, text not found...)."""


class Workspace:
    """The agent's view of the app copy: path checks, guarded edits, and a diff."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.original = {self.rel(p): p.read_text(encoding="utf-8", errors="ignore") for p in self._files()}

    def _files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*")
                      if p.is_file() and not SKIP_DIRS & set(p.relative_to(self.root).parts))

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def resolve(self, path: str) -> Path:
        full = (self.root / path.strip().lstrip("/\\")).resolve()
        if full != self.root and self.root not in full.parents:
            raise ToolError(f"{path} is outside the app folder")
        if SKIP_DIRS & set(full.relative_to(self.root).parts):
            raise ToolError(f"{path} is in a folder the agent may not touch")
        return full

    def list_files(self, path: str = ".") -> str:
        base = self.resolve(path or ".")
        files = [p for p in self._files() if base == p or base in p.parents]
        return "\n".join(f"{self.rel(p)} ({len(p.read_text(encoding='utf-8', errors='ignore').splitlines())} lines)"
                         for p in files) or "No files."

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        full = self.resolve(path)
        if not full.is_file():
            raise ToolError(f"{path} does not exist")
        lines = full.read_text(encoding="utf-8", errors="ignore").splitlines()
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        return "\n".join(f"{n:>4}  {lines[n - 1]}" for n in range(start, end + 1))

    def search_code(self, pattern: str) -> str:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ToolError(f"Invalid regular expression: {exc}") from exc
        hits = []
        for p in self._files():
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{self.rel(p)}:{n}: {line.strip()[:200]}")
        return "\n".join(hits[:60]) or "No matches."

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        full = self.resolve(path)
        rel = self.rel(full)
        if rel in self.original and TEST_FILE.search(rel):
            raise ToolError("Existing tests must not be changed; add a new test file instead.")
        if not full.is_file():
            raise ToolError(f"{path} does not exist (use write_file to create new files)")
        text = full.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ToolError(f"old_text occurs {count} times in {path}; it must occur exactly once. "
                            "Copy it exactly from read_file output, without the line numbers.")
        full.write_text(text.replace(old_text, new_text), encoding="utf-8")
        return f"Edited {rel}."

    def write_file(self, path: str, content: str) -> str:
        full = self.resolve(path)
        if full.exists():
            raise ToolError(f"{path} already exists; change it with edit_file.")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return f"Created {self.rel(full)}."

    def changed(self) -> dict[str, str]:
        """{path: new content} for every file added or modified."""
        out = {}
        for p in self._files():
            rel, text = self.rel(p), p.read_text(encoding="utf-8", errors="ignore")
            if self.original.get(rel) != text:
                out[rel] = text
        return out

    def diff(self) -> str:
        chunks = []
        for rel, text in self.changed().items():
            before = self.original.get(rel, "")
            chunks.append("".join(difflib.unified_diff(
                before.splitlines(keepends=True), text.splitlines(keepends=True),
                fromfile=f"a/{rel}" if rel in self.original else "/dev/null", tofile=f"b/{rel}")))
        return "\n".join(chunks)


def _first_message(report: ReproReport, test_code: str, red_message: str, triaged: Triage | None) -> str:
    hints = ""
    if triaged and triaged.files:
        ranked = "\n".join(f"- {f.path}: {f.reason}" for f in triaged.files)
        hints = f"\n\nTriage (a fast model's guess):\nHypothesis: {triaged.hypothesis}\nStart with:\n{ranked}"
    report_json = report.model_dump_json(include={"title", "steps", "expected_behavior", "actual_behavior",
                                                  "error_text"}, indent=1)
    return (f"Bug report:\n{report_json}\n\nRegression test (Playwright, runs from outside the app):\n"
            f"```python\n{test_code}```\n\nIt currently fails like this:\n{red_message}{hints}")


def _assistant_dict(message) -> dict:
    """The assistant turn in the shape the API expects back in `messages`."""
    out: dict = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        out["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.function.name, "arguments": c.function.arguments}}
                             for c in message.tool_calls]
    return out


def fix(
    report: ReproReport,
    test_code: str,
    red_message: str,
    app_dir: Path,
    triaged: Triage | None = None,
    tracker: UsageTracker | None = None,
    on_step: Callable[[FixStep], None] | None = None,
    on_research: Callable[[web.ResearchEntry], None] | None = None,
    on_escalate: Callable[[str], None] | None = None,
) -> FixResult:
    """Patch `app_dir` in place until `test_code` and the existing suite pass, or give up."""
    tracker = tracker or UsageTracker()
    ws = Workspace(app_dir)
    result = FixResult(fixed=False)
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": _first_message(report, test_code, red_message, triaged)}]
    model = settings.models.coder
    failed_runs = 0
    green_since_edit = False

    def escalate(reason: str) -> None:
        nonlocal model
        if result.escalated:
            return
        model, result.escalated = settings.models.escalate, True
        if on_escalate:
            on_escalate(reason)
        messages.append({"role": "user", "content": f"(A senior engineer takes over: {reason}. Review what was "
                                                    "tried so far and fix the bug.)"})

    def run(name: str, args: dict) -> str:
        nonlocal failed_runs, green_since_edit
        if name == "list_files":
            return ws.list_files(args.get("path", "."))
        if name == "read_file":
            return ws.read_file(args["path"], args.get("start_line"), args.get("end_line"))
        if name == "search_code":
            return ws.search_code(args["pattern"])
        if name in ("edit_file", "write_file"):
            green_since_edit = False
            return (ws.edit_file(args["path"], args["old_text"], args["new_text"]) if name == "edit_file"
                    else ws.write_file(args["path"], args["content"]))
        if name == "run_tests":
            if not ws.changed():
                return "Nothing has changed yet: the regression test is still red. Edit the code first."
            checks = run_checks(app_dir, test_code)
            result.checks = checks
            green_since_edit = checks.green
            if not checks.green:
                failed_runs += 1
            return ("ALL GREEN. Call finish.\n\n" if checks.green else "NOT GREEN YET.\n\n") + checks.summary()
        if name in ("web_search", "read_url"):
            entry = web.search(args["query"]) if name == "web_search" else web.extract(args["url"])
            result.research.append(entry)
            if on_research:
                on_research(entry)
            return web.as_tool_result(entry)
        if name == "finish":
            if not green_since_edit:
                raise ToolError("Not finished: run_tests must be fully green after your last change.")
            result.fixed, result.root_cause, result.summary = True, args.get("root_cause", ""), args.get("summary", "")
            return "Done."
        raise ToolError(f"Unknown tool {name}")

    for n in range(1, MAX_STEPS + 1):
        if failed_runs >= ESCALATE_AFTER_FAILED_RUNS:
            escalate(f"the fix failed the tests {failed_runs} times")
        elif n > ESCALATE_AFTER_STEPS and not green_since_edit:
            escalate(f"no green test run after {ESCALATE_AFTER_STEPS} steps")

        stage = "fix:escalate" if result.escalated else "fix"
        try:
            message = chat_tools(stage=f"{stage}#{n}", model=model, messages=messages, tools=TOOLS, tracker=tracker)
        except TruncatedReply:
            message = chat_tools(stage=f"{stage}#{n}:no-think", model=model, messages=messages, tools=TOOLS,
                                 tracker=tracker, thinking=False)
        messages.append(_assistant_dict(message))

        if not message.tool_calls:
            messages.append({"role": "user", "content": "Continue with the tools. Call finish only after run_tests "
                                                        "is fully green."})
            continue

        for call in message.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
                output, ok = run(call.function.name, args), True
            except (ToolError, KeyError, json.JSONDecodeError, web.ResearchError) as exc:
                args = {} if isinstance(exc, json.JSONDecodeError) else args
                output, ok = f"Error: {exc}", False
            step = FixStep(n=len(result.steps) + 1, model=model, tool=call.function.name,
                           args={k: (v if len(str(v)) < 400 else str(v)[:400] + "...") for k, v in args.items()},
                           result=output[:800], ok=ok)
            result.steps.append(step)
            if on_step:
                on_step(step)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output[:TOOL_RESULT_CHARS]})

        if result.fixed:
            break

    result.files = ws.changed()
    result.changed_files = list(result.files)
    result.diff = ws.diff()
    return result
