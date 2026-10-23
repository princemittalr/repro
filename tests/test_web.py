"""Offline tests for the web layer: routes, the landing page's real-run data and its honesty.

    python -m unittest tests.test_web

No model calls: only static pages, the recorded demo runs and the privacy rules for live runs.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import showcase
from app.main import SAMPLES, STATIC_DIR, app
from app.runs import DEMO_DIR, RunStore

client = TestClient(app)


class RoutesTest(unittest.TestCase):
    def test_landing_page_at_root_and_tool_at_app(self):
        landing, tool = client.get("/"), client.get("/app")
        self.assertEqual(landing.status_code, 200)
        self.assertIn("It ships the fix", landing.text)
        self.assertEqual(tool.status_code, 200)
        self.assertIn('id="dropzone"', tool.text)

    def test_old_run_links_redirect_to_the_app(self):
        resp = client.get("/?run=abc123", follow_redirects=False)
        self.assertEqual(resp.status_code, 308)
        self.assertEqual(resp.headers["location"], "/app?run=abc123")
        self.assertEqual(client.get("/try", follow_redirects=False).headers["location"], "/app")

    def test_static_assets_are_served(self):
        for path in ["/static/css/site.css", "/static/js/app.js", "/static/js/landing.js", "/static/js/fx.js",
                     "/static/img/og.png", "/static/fonts/geist-latin-wght-normal.woff2",
                     *[f"/static/img/poster-{name}.jpg" for name in SAMPLES]]:
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_samples_have_posters(self):
        for sample in client.get("/api/samples").json():
            self.assertTrue(sample["poster_url"].endswith(f"poster-{sample['name']}.jpg"))


class PagesTest(unittest.TestCase):
    pages = {name: (STATIC_DIR / name).read_text(encoding="utf-8") for name in ["landing.html", "app.html"]}

    def test_no_tailwind_play_cdn_and_built_css_exists(self):
        for name, html in self.pages.items():
            self.assertNotIn("cdn.tailwindcss.com", html, name)
            self.assertIn("/static/css/site.css", html, name)
        self.assertGreater((STATIC_DIR / "css" / "site.css").stat().st_size, 10_000)

    def test_open_graph_tags(self):
        for name, html in self.pages.items():
            for tag in ["og:title", "og:description", "og:image", "twitter:card"]:
                self.assertIn(f'property="{tag}"' if tag.startswith("og") else f'name="{tag}"', html, f"{name}: {tag}")

    def test_one_h1_on_the_landing_page(self):
        self.assertEqual(len(re.findall(r"<h1\b", self.pages["landing.html"])), 1)

    def test_no_dev_internals_in_the_ui(self):
        app_js = (STATIC_DIR / "js" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("app/usage.py", app_js)
        self.assertNotIn("✕", app_js)  # a red test in REPRODUCE is "bug caught", not an error cross

    def test_one_h1_on_the_app_page(self):
        # The start and run screens share a page; app.js keeps only the visible screen's heading an <h1>.
        self.assertEqual(len(re.findall(r"<h1\b", self.pages["app.html"])), 1)
        self.assertIn("function setHeading", (STATIC_DIR / "js" / "app.js").read_text(encoding="utf-8"))

    def test_nav_links_match_on_both_pages(self):
        nav = lambda html: re.findall(r'class="nav-link" href="([^"]+)"', html)
        self.assertEqual(nav(self.pages["app.html"]), nav(self.pages["landing.html"]))

    def test_only_semantic_colour_tokens(self):
        # Raw palette classes don't compile (tailwind.config.js defines only tokens), so they'd silently vanish.
        raw = re.compile(r"\b(?:bg|text|border|from|via|to|ring|shadow|fill|stroke)-(?:slate|gray|zinc|emerald|green|rose|red|sky|blue|amber|yellow)-\d")
        for path in [*STATIC_DIR.glob("*.html"), *(STATIC_DIR / "js").glob("*.js")]:
            self.assertIsNone(raw.search(path.read_text(encoding="utf-8")), path.name)

    def test_feature_frames_are_real_keyframes(self):
        for src in re.findall(r'src="/static/(img/frame-coupon-[^"]+)"', self.pages["landing.html"]):
            self.assertTrue((STATIC_DIR / src).exists(), src)
        demo = json.loads((DEMO_DIR / "coupon.json").read_text(encoding="utf-8"))
        self.assertIn("11.94", json.dumps(demo))

    def test_reduced_motion_is_handled(self):
        css = (STATIC_DIR / "css" / "site.src.css").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("FX.reduced()", (STATIC_DIR / "js" / "landing.js").read_text(encoding="utf-8"))


class LandingNumbersTest(unittest.TestCase):
    """Every number on the landing page must come from a recorded run (honesty rule)."""

    def setUp(self):
        self.runs = {r["sample"]: r for r in showcase.recent_runs(SAMPLES) if r["source"] == "recorded"}
        self.html = (STATIC_DIR / "landing.html").read_text(encoding="utf-8")

    def test_coupon_run_numbers(self):
        coupon = self.runs["coupon"]
        self.assertTrue(coupon["success"])
        self.assertEqual(round(coupon["duration_s"]), 91)
        self.assertEqual((coupon["tests_passed"], coupon["tests_total"]), (8, 8))
        self.assertEqual(round(coupon["confidence"] * 100), 98)
        self.assertEqual(coupon["pull_request"]["number"], 3)
        self.assertIn('data-count="91"', self.html)
        self.assertIn('data-count="98"', self.html)
        self.assertIn(f"{coupon['tokens']:,} tokens", self.html)
        self.assertIn(coupon["pull_request"]["url"], self.html)

    def test_remove_run_numbers(self):
        remove = self.runs["remove"]
        self.assertTrue(remove["success"])
        self.assertEqual((remove["tests_passed"], remove["tests_total"]), (9, 9))
        self.assertIn("1:02", self.html)
        self.assertIn(f"{remove['tokens']:,} tokens", self.html)

    def test_quoted_root_causes_are_the_agents_own_words(self):
        for sample in ["coupon", "remove"]:
            quote = self.runs[sample]["root_cause"].split(",")[0]
            self.assertIn(quote, self.html, sample)

    def test_quantity_sample_has_no_replay(self):
        # The landing page says the quantity bug is live only; that must stay true.
        self.assertFalse((DEMO_DIR / "quantity.json").exists())


class RecentRunsPrivacyTest(unittest.TestCase):
    def _saved(self, video_name: str, pr_url: str | None) -> dict:
        saved = json.loads((DEMO_DIR / "coupon.json").read_text(encoding="utf-8"))
        saved["video_name"] = video_name
        saved["result"]["pull_request"] = {"number": 9, "url": pr_url} if pr_url else None
        return saved

    def test_live_runs_never_expose_text_from_the_video(self):
        line = showcase.summarize(self._saved("my-secret-app.mp4", None), "live", SAMPLES)
        self.assertEqual(line["title"], "An uploaded recording")
        for key in ["bug", "root_cause", "summary", "changed_files", "confidence", "steps"]:
            self.assertIsNone(line[key], key)
        self.assertTrue(line["success"])

    def test_live_pr_links_only_for_the_public_demo_repo(self):
        settings = mock.Mock(github_demo_repo="princemittalr/shoplite")
        with mock.patch.object(showcase, "settings", settings):
            public = showcase.summarize(self._saved("shoplite-coupon.mp4", "https://github.com/princemittalr/shoplite/pull/9"), "live", SAMPLES)
            private = showcase.summarize(self._saved("shoplite-coupon.mp4", "https://github.com/someone/private/pull/9"), "live", SAMPLES)
        self.assertEqual(public["title"], "Coupon stacks")
        self.assertEqual(public["pull_request"]["number"], 9)
        self.assertIsNone(private["pull_request"])

    def test_recent_endpoint_lists_live_runs_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "abc.json").write_text(json.dumps(self._saved("upload.webm", None)), encoding="utf-8")
            with mock.patch.object(showcase, "RUNS_DIR", Path(tmp)):
                data = client.get("/api/recent").json()
        self.assertEqual(data[0]["source"], "live")
        self.assertEqual(data[0]["title"], "An uploaded recording")
        self.assertEqual({r["sample"] for r in data if r["source"] == "recorded"}, {"coupon", "remove"})


class ReplayClockTest(unittest.TestCase):
    def test_replay_reports_the_original_run_duration(self):
        run = RunStore().start_replay(DEMO_DIR / "coupon.json", Path("video.mp4"), "shoplite-coupon.mp4")
        self.assertEqual(round(run.summary()["recorded_duration_s"]), 91)


if __name__ == "__main__":
    unittest.main()


class ColdStartTest(unittest.TestCase):
    def test_public_get_endpoints_allow_cross_origin_reads(self):
        resp = client.get("/api/health", headers={"Origin": "https://repro.example.com"})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "*")
        pre = client.options("/api/runs", headers={"Origin": "https://repro.example.com", "Access-Control-Request-Method": "POST"})
        self.assertEqual(pre.status_code, 400)  # only GET is opened up: no cross-origin run starts

    def test_static_build_points_the_tool_at_the_api(self):
        from scripts.build_static import build
        with tempfile.TemporaryDirectory() as tmp:
            out = build("https://api.example.com", "https://repro.example.com", Path(tmp) / "site")
            html = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn('window.REPRO_API = "https://api.example.com"', html)
            self.assertIn('href="https://api.example.com/app?sample=coupon', html)
            self.assertNotRegex(html, r'href="/app')
            self.assertIn('content="https://repro.example.com/static/img/og.png"', html)
            self.assertTrue((out / "media" / "samples" / "coupon.mp4").exists())
            self.assertTrue((out / "static" / "css" / "site.css").exists())
