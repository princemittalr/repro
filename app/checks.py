"""Run every check that decides whether a fix is done.

1. The regression test from REPRODUCE (Playwright, in the sandbox) against a
   fresh start of the patched app: it must now PASS.
2. The app's own test suite (`npm test` when package.json defines one): it
   must still pass, so the fix breaks nothing else.

The fix agent calls this through its `run_tests` tool, and the VERIFY stage
calls it once more on the final code, so both use exactly the same rules.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel

from app.sandbox import run_test
from app.schemas import TestRun
from app.target import running_app

SUITE_TIMEOUT_S = 120


class SuiteRun(BaseModel):
    command: str | None = None  # None when the app has no test suite
    passed: bool = True
    output: str = ""


class Checks(BaseModel):
    repro: TestRun
    suite: SuiteRun

    @property
    def green(self) -> bool:
        return self.repro.outcome == "passed" and self.suite.passed

    def summary(self) -> str:
        repro = ("Regression test: PASSED" if self.repro.outcome == "passed"
                 else f"Regression test: {self.repro.outcome.upper()}\n{self.repro.message[:1500]}")
        if self.suite.command is None:
            suite = "Existing test suite: none found"
        else:
            state = "PASSED" if self.suite.passed else "FAILED"
            suite = f"Existing test suite (`{self.suite.command}`): {state}\n{self.suite.output[-1500:]}"
        return f"{repro}\n\n{suite}"


def run_suite(app_dir: Path) -> SuiteRun:
    package = app_dir / "package.json"
    if not package.exists() or "test" not in json.loads(package.read_text(encoding="utf-8")).get("scripts", {}):
        return SuiteRun()
    npm = shutil.which("npm")
    try:
        proc = subprocess.run([npm, "test"], cwd=app_dir, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=SUITE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return SuiteRun(command="npm test", passed=False, output=f"Timed out after {SUITE_TIMEOUT_S}s")
    return SuiteRun(command="npm test", passed=proc.returncode == 0, output=(proc.stdout + proc.stderr).strip())


def run_checks(app_dir: Path, test_code: str) -> Checks:
    """Start the app from `app_dir` as it is now, run the regression test, then the suite."""
    with tempfile.TemporaryDirectory() as tmp:
        # running_app copies the folder, so the app under test is exactly the current code.
        with running_app(app_dir) as (base_url, _):
            repro = run_test(test_code, base_url, Path(tmp) / "artifacts")
    return Checks(repro=repro, suite=run_suite(app_dir))
