"""TRIAGE: rank the files most likely to contain the bug, before the fix loop.

A cheap pre-step on Nemotron 3.5 Lightning (thinking off): it sees the bug
report and a compact map of the repository (paths, sizes and the names of
the functions each source file defines) and returns a short ranked list.
The fix agent starts by reading those files instead of exploring blindly,
which saves tool calls and tokens on the expensive coder model.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import settings
from app.llm import chat_json
from app.schemas import ReproReport
from app.usage import UsageTracker

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", "coverage", "e2e"}
CODE_SUFFIXES = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".py", ".html", ".css", ".vue", ".svelte", ".json"}
_DEFINITIONS = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|def\s+(\w+)|class\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\()",
    re.MULTILINE,
)


class RankedFile(BaseModel):
    path: str
    reason: str = Field(description="One short sentence: why this file is likely involved")


class Triage(BaseModel):
    files: list[RankedFile] = Field(description="Most likely first, at most 5")
    hypothesis: str = Field(description="One-sentence guess at the root cause")


def source_files(app_dir: Path) -> list[Path]:
    return sorted(p for p in app_dir.rglob("*")
                  if p.is_file() and p.suffix in CODE_SUFFIXES
                  and not SKIP_DIRS & set(p.relative_to(app_dir).parts) and p.stat().st_size < 300_000)


def repo_map(app_dir: Path) -> str:
    """One line per source file: path, size and the functions/classes it defines."""
    lines = []
    for path in source_files(app_dir):
        text = path.read_text(encoding="utf-8", errors="ignore")
        names = [next(g for g in m.groups() if g) for m in _DEFINITIONS.finditer(text)]
        defined = f"  defines: {', '.join(names[:25])}" if names else ""
        lines.append(f"{path.relative_to(app_dir).as_posix()} ({len(text.splitlines())} lines){defined}")
    return "\n".join(lines)


PROMPT = """You are triaging a bug in a code repository. From the bug report and the repository map, pick
the files a developer should read first to find and fix the root cause (at most 5, most likely
first). Prefer the files holding the business logic over UI wiring, and skip tests and configuration
unless the report points at them.

Bug report:
{report}

Repository map (path, line count, definitions):
{repo_map}

Reply with JSON: {{"files": [{{"path": ..., "reason": ...}}], "hypothesis": ...}}"""


def triage(report: ReproReport, app_dir: Path, tracker: UsageTracker | None = None) -> Triage:
    summary = report.model_dump_json(include={"title", "page", "steps", "expected_behavior", "actual_behavior",
                                              "error_text"}, indent=1)
    data = chat_json(
        stage="triage", model=settings.models.triage, tracker=tracker, temperature=0, max_tokens=2000,
        thinking=False, schema=Triage.model_json_schema(), schema_name="Triage",
        messages=[{"role": "user", "content": PROMPT.format(report=summary, repo_map=repo_map(app_dir))}],
    )
    result = Triage.model_validate(data)
    # Keep only paths that really exist, so the fix agent never starts from a hallucinated file.
    known = {p.relative_to(app_dir).as_posix() for p in source_files(app_dir)}
    result.files = [f for f in result.files if f.path in known][:5]
    return result
