"""Safely run model-written Playwright tests.

Generated code is untrusted, so before it runs it must pass a static check
(only Playwright and `re` may be imported, no file/OS/process access). It
then runs in a separate Python process with a hard timeout, via `app.harness`.
In production the whole agent also runs inside a container.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import PROJECT_ROOT
from app.schemas import TestRun

ALLOWED_IMPORTS = {"re", "playwright.sync_api"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "getattr", "setattr", "input"}
TEST_TIMEOUT_S = 60


class UnsafeCode(ValueError):
    pass


def check_code(code: str) -> None:
    """Reject generated code that does anything beyond driving the browser."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise UnsafeCode(f"Syntax error on line {exc.lineno}: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name not in ALLOWED_IMPORTS]
            if bad:
                raise UnsafeCode(f"Import not allowed: {', '.join(bad)}")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in ALLOWED_IMPORTS:
                raise UnsafeCode(f"Import not allowed: {node.module}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise UnsafeCode(f"Use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsafeCode(f"Access to '{node.attr}' is not allowed")

    if not any(isinstance(n, ast.FunctionDef) and n.name == "test_repro" for n in tree.body):
        raise UnsafeCode("The test must define a top-level function test_repro(page, base_url)")


def run_test(code: str, base_url: str, artifacts_dir: Path) -> TestRun:
    """Statically check, then run, one generated test against `base_url`."""
    try:
        check_code(code)
    except UnsafeCode as exc:
        return TestRun(outcome="rejected", message=str(exc))

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        test_file = Path(tmp) / "repro_test.py"
        result_file = Path(tmp) / "result.json"
        test_file.write_text(code, encoding="utf-8")
        try:
            subprocess.run(
                [sys.executable, "-m", "app.harness", str(test_file), base_url, str(result_file), str(artifacts_dir)],
                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=TEST_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return TestRun(outcome="timeout", message=f"The test did not finish within {TEST_TIMEOUT_S}s.")
        if not result_file.exists():
            return TestRun(outcome="error", message="The test harness crashed before reporting a result.")
        return TestRun.model_validate(json.loads(result_file.read_text(encoding="utf-8")))


def page_snapshot(base_url: str) -> dict:
    """Describe the app's start page for the test-writing model (runs in a subprocess)."""
    with tempfile.TemporaryDirectory() as tmp:
        result_file = Path(tmp) / "snapshot.json"
        proc = subprocess.run(
            [sys.executable, "-m", "app.harness", "--snapshot", base_url, str(result_file)],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=TEST_TIMEOUT_S,
        )
        if not result_file.exists():
            raise RuntimeError(f"Could not capture the page: {proc.stderr[-500:]}")
        return json.loads(result_file.read_text(encoding="utf-8"))
