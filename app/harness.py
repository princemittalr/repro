"""Sandbox harness: run ONE generated Playwright test and report the outcome as JSON.

It always runs in its own subprocess (see `app.sandbox.run_test`), so a hanging
or crashing generated test can never take down the Repro server.

    python -m app.harness <test_file> <base_url> <result_json> <artifacts_dir>
    python -m app.harness --snapshot <base_url> <result_json>

The test file must define `test_repro(page, base_url)`. `--snapshot` instead
records what the start page looks like to a test author (accessibility tree,
test ids, trimmed HTML) so the model can pick robust selectors.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ACTION_TIMEOUT_MS = 5_000
EXPECT_TIMEOUT_MS = 3_000


def _load(test_file: Path):
    spec = importlib.util.spec_from_file_location("repro_test", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _short_trace(exc: BaseException) -> str:
    """The exception plus the line of the generated test where it happened."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename.endswith("repro_test.py")]
    where = f" (test line {frames[-1].lineno}: {frames[-1].line})" if frames else ""
    return f"{type(exc).__name__}: {exc}{where}"


def main(test_file: Path, base_url: str, result_file: Path, artifacts: Path) -> None:
    artifacts.mkdir(parents=True, exist_ok=True)
    result: dict = {"outcome": "error", "message": "", "duration_s": 0.0}
    start = time.perf_counter()

    try:
        module = _load(test_file)
    except Exception as exc:  # noqa: BLE001 - syntax/import errors in generated code
        result["message"] = f"Could not load the test: {type(exc).__name__}: {exc}"
        result_file.write_text(json.dumps(result), encoding="utf-8")
        return

    expect.set_options(timeout=EXPECT_TIMEOUT_MS)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        try:
            module.test_repro(page, base_url)
            result["outcome"] = "passed"
        except AssertionError as exc:
            result["outcome"] = "failed"
            result["message"] = _short_trace(exc)
        except Exception as exc:  # noqa: BLE001 - any other error means the test itself is broken
            result["outcome"] = "error"
            result["message"] = _short_trace(exc)

        if result["outcome"] != "passed":
            try:
                shot = artifacts / "failure.png"
                page.screenshot(path=str(shot))
                result["screenshot"] = str(shot)
                result["page_snapshot"] = page.locator("body").aria_snapshot()[:6000]
            except Exception:  # noqa: BLE001 - best effort only
                pass
        browser.close()

    result["duration_s"] = round(time.perf_counter() - start, 2)
    result_file.write_text(json.dumps(result), encoding="utf-8")


# Runs in the page: list interactive and test-id elements with their visible text.
_ELEMENTS_JS = """() => Array.from(document.querySelectorAll(
  'button, a, input, select, textarea, [role], [data-testid]')).slice(0, 200).map(el => ({
    tag: el.tagName.toLowerCase(),
    testid: el.getAttribute('data-testid'),
    id: el.id || null,
    role: el.getAttribute('role'),
    text: (el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '')
      .trim().slice(0, 60),
}))"""

_HTML_JS = """() => { const b = document.body.cloneNode(true);
  b.querySelectorAll('script, style, svg').forEach(n => n.remove());
  return b.outerHTML.replace(/\\s+/g, ' '); }"""


def snapshot(base_url: str, result_file: Path) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(base_url, wait_until="networkidle")
        result = {
            "url": page.url,
            "title": page.title(),
            "aria": page.locator("body").aria_snapshot()[:8000],
            "elements": page.evaluate(_ELEMENTS_JS),
            "html": page.evaluate(_HTML_JS)[:12000],
        }
        browser.close()
    result_file.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "--snapshot":
        snapshot(sys.argv[2], Path(sys.argv[3]))
    else:
        main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
