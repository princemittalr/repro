"""Export the landing page as a static site, so it can be served from a CDN with no cold start.

The free Render instance sleeps after 15 minutes idle and takes 30-60 seconds to wake. Hosting the
landing page on Netlify, Cloudflare Pages or GitHub Pages makes the first impression instant; the
page then wakes the API in the background (GET /api/health) while the visitor reads the hero, and
every "try it" link points at the API origin, where the tool lives.

    python -m scripts.build_static                     # writes dist/site
    REPRO_API_ORIGIN=https://my-api.example.com SITE_URL=https://repro.example.com python -m scripts.build_static
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
OUT = ROOT / "dist" / "site"
DEFAULT_API = "https://repro-yrgp.onrender.com"


def build(api: str, site: str, out: Path = OUT) -> Path:
    api, site = api.rstrip("/"), site.rstrip("/")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out / "static", ignore=shutil.ignore_patterns("*.src.css", "app.html", "landing.html"))
    # The three sample recordings are small (~400 KB together): serve them from the CDN too.
    media = out / "media" / "samples"
    media.mkdir(parents=True)
    for video in (ROOT / "samples").glob("shoplite-*.mp4"):
        shutil.copy2(video, media / f"{video.stem.removeprefix('shoplite-')}.mp4")

    html = (STATIC / "landing.html").read_text(encoding="utf-8")
    # The tool and the API stay on the server; everything else is local to the static site.
    html = re.sub(r'(href|src)="/(app|try|api)\b', rf'\1="{api}/\2', html)
    html = re.sub(r'(content|href)="https://repro-yrgp\.onrender\.com', rf'\1="{site}', html)
    html = html.replace("<head>", f'<head>\n  <script>window.REPRO_API = "{api}";</script>', 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "404.html").write_text(html, encoding="utf-8")
    # Netlify and Cloudflare Pages both read _redirects: old shared links to the tool keep working.
    (out / "_redirects").write_text(f"/app* {api}/app:splat 302\n/try {api}/app 302\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    api = os.environ.get("REPRO_API_ORIGIN", DEFAULT_API)
    site = os.environ.get("SITE_URL", api)
    path = build(api, site)
    print(f"Static landing page written to {path} (API: {api}, canonical: {site})")
