"""Record realistic bug-report videos of ShopLite with Playwright.

Usage:
    python -m scripts.record_sample              # records every scenario
    python -m scripts.record_sample coupon       # records one scenario

Each scenario is played at human speed with a visible cursor and click ripple,
so the clip looks like a real QA screen recording. Output: samples/<name>.mp4.
These clips power the "Try with a sample video" button in later phases.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app.config import PROJECT_ROOT

SHOPLITE_DIR = PROJECT_ROOT / "shoplite"
SAMPLES_DIR = PROJECT_ROOT / "samples"
PORT = 3100
BASE_URL = f"http://localhost:{PORT}"

# Injected into every page: draws a fake cursor and a ripple on each click,
# because headless browser recordings don't show the mouse pointer.
CURSOR_JS = """
window.addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
  dot.style.cssText = 'position:fixed;z-index:99999;width:18px;height:18px;border-radius:50%;' +
    'background:rgba(239,68,68,.85);border:2px solid #fff;pointer-events:none;' +
    'transform:translate(-50%,-50%);transition:left .25s,top .25s;left:-50px;top:-50px';
  document.body.appendChild(dot);
  document.addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; });
  document.addEventListener('mousedown', e => {
    const r = document.createElement('div');
    r.style.cssText = 'position:fixed;z-index:99998;width:40px;height:40px;border-radius:50%;' +
      'border:3px solid rgba(239,68,68,.9);pointer-events:none;transform:translate(-50%,-50%) scale(.3);' +
      'transition:transform .4s,opacity .4s;left:' + e.clientX + 'px;top:' + e.clientY + 'px';
    document.body.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'translate(-50%,-50%) scale(1.4)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 450);
  });
});
"""


# Ground-truth log of what each scenario actually did, used by eval_perception.
_events: list[dict] = []
_t0 = 0.0


def _log(action: str, test_id: str, value: str | None = None) -> None:
    _events.append({"action": action, "test_id": test_id, "value": value,
                    "timestamp_s": round(time.perf_counter() - _t0, 2)})


def click(page: Page, test_id: str, pause: float = 0.9) -> None:
    """Move the visible cursor to an element, click it, and pause like a human."""
    box = page.get_by_test_id(test_id).bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12)
    page.wait_for_timeout(250)
    _log("click", test_id)
    page.get_by_test_id(test_id).click()
    page.wait_for_timeout(int(pause * 1000))


def type_into(page: Page, test_id: str, text: str) -> None:
    click(page, test_id, pause=0.3)
    _events.pop()  # focusing the field is part of the "type" action, not a separate click
    _log("type", test_id, text)
    page.get_by_test_id(test_id).press_sequentially(text, delay=120)
    page.wait_for_timeout(500)


def scenario_coupon(page: Page) -> None:
    """Bug: SAVE10 can be applied twice, so the discount doubles."""
    click(page, "add-tee")
    click(page, "add-mug")
    type_into(page, "coupon-input", "SAVE10")
    click(page, "apply-coupon", pause=1.5)
    type_into(page, "coupon-input", "SAVE10")
    click(page, "apply-coupon", pause=2.5)


def scenario_remove(page: Page) -> None:
    """Bug: removing an item leaves its price in the subtotal."""
    click(page, "add-cap")
    click(page, "add-stickers")
    click(page, "add-mug", pause=1.5)
    click(page, "remove-cap", pause=3.0)


def scenario_quantity(page: Page) -> None:
    """Bug: the minus button lets quantity reach 0 and go negative."""
    click(page, "add-tee", pause=1.2)
    click(page, "dec-tee", pause=1.2)
    click(page, "dec-tee", pause=1.2)
    click(page, "dec-tee", pause=2.5)


SCENARIOS = {"coupon": scenario_coupon, "remove": scenario_remove, "quantity": scenario_quantity}


def record(name: str) -> Path:
    SAMPLES_DIR.mkdir(exist_ok=True)
    out = SAMPLES_DIR / f"shoplite-{name}.mp4"
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=tmp,
            record_video_size={"width": 1280, "height": 720},
        )
        context.add_init_script(CURSOR_JS)
        global _t0
        _events.clear()
        page = context.new_page()  # video recording starts with the page
        _t0 = time.perf_counter()
        page.goto(BASE_URL)
        page.wait_for_timeout(1200)
        SCENARIOS[name](page)
        context.close()  # finalises the .webm
        browser.close()

        webm = next(Path(tmp).glob("*.webm"))
        # Re-encode to H.264 MP4: smaller, and accepted everywhere (including the model API).
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-crf", "28", "-movflags", "+faststart", str(out)],
            check=True,
        )
    truth = SAMPLES_DIR / f"shoplite-{name}.events.json"
    truth.write_text(json.dumps({"scenario": name, "events": _events}, indent=2), encoding="utf-8")
    return out


def main() -> None:
    names = sys.argv[1:] or list(SCENARIOS)
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required to run ShopLite (node not found on PATH).")

    server = subprocess.Popen([node, "server.js"], cwd=SHOPLITE_DIR, env={**__import__("os").environ, "PORT": str(PORT)})
    try:
        time.sleep(1.5)  # give the server a moment to bind the port
        for name in names:
            path = record(name)
            print(f"Recorded {path.relative_to(PROJECT_ROOT)}")
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
