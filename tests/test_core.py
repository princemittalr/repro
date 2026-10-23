"""Fast, offline unit tests for the parts of Repro that need no model or network.

    python -m unittest discover tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.events import EventBus
from app.fix import ToolError, Workspace
from app.perception import FrameState, timeline
from app.reproduce import source_elements
from app.sandbox import UnsafeCode, check_code
from app.ship import e2e_test_file, slugify
from app.triage import repo_map


class TimelineTest(unittest.TestCase):
    def test_change_is_credited_to_pointer_before_it(self):
        states = [
            FrameState(t=1.0, pointer="none", click=False, state=["Cart > A: 1", "Cart > B: 1", "Total: $2"]),
            FrameState(t=2.0, pointer="Remove (A row)", click=True, state=["Cart > A: 1", "Cart > B: 1", "Total: $2"]),
            FrameState(t=2.5, pointer="Remove (B row)", click=True, state=["Cart > B: 1", "Total: $2"]),
        ]
        text = timeline(states)
        self.assertIn("CHANGE, triggered on: Remove (A row)", text)
        self.assertIn("disappeared: Cart > A: 1", text)
        # The before-frame is folded into the change line, not listed as a second click.
        self.assertNotIn("t=2.00s  pointer:", text)

    def test_value_changes_are_paired_by_key(self):
        states = [FrameState(t=0, pointer="none", click=False, state=["Total: $5.00"]),
                  FrameState(t=1, pointer="none", click=False, state=["Total: $-5.00"])]
        self.assertIn("changed: Total: $5.00  ->  $-5.00", timeline(states))


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "test").mkdir()
        (self.root / "src" / "cart.js").write_text("const a = 1;\nconst b = 2;\n", encoding="utf-8")
        (self.root / "test" / "cart.test.js").write_text("// existing test\n", encoding="utf-8")
        self.ws = Workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_edit_and_diff(self):
        self.ws.edit_file("src/cart.js", "const b = 2;", "const b = 3;")
        self.assertEqual(list(self.ws.changed()), ["src/cart.js"])
        self.assertIn("+const b = 3;", self.ws.diff())

    def test_edit_requires_a_unique_match(self):
        with self.assertRaises(ToolError):
            self.ws.edit_file("src/cart.js", "const", "let")

    def test_edit_with_wrong_indentation_is_reindented(self):
        # A real failure: the model copied a 2-space function body with 4 spaces, every time.
        (self.root / "src" / "fn.js").write_text("function f(x) {\n  if (x) {\n    return 1;\n  }\n  return 0;\n}\n",
                                                 encoding="utf-8")
        self.ws.edit_file("src/fn.js", "function f(x) {\n    if (x) {\n      return 1;\n    }",
                          "function f(x) {\n    if (x) {\n      return 2;\n    }\n    if (!x) return 3;")
        self.assertEqual((self.root / "src" / "fn.js").read_text(encoding="utf-8"),
                         "function f(x) {\n  if (x) {\n    return 2;\n  }\n  if (!x) return 3;\n  return 0;\n}\n")

    def test_existing_tests_are_protected_but_new_ones_allowed(self):
        with self.assertRaises(ToolError):
            self.ws.edit_file("test/cart.test.js", "existing", "weakened")
        self.ws.write_file("test/coupon.test.js", "// new test\n")
        self.assertIn("test/coupon.test.js", self.ws.changed())

    def test_paths_cannot_escape_the_app(self):
        with self.assertRaises(ToolError):
            self.ws.read_file("../outside.txt")
        with self.assertRaises(ToolError):
            self.ws.write_file("node_modules/x.js", "")


class SandboxTest(unittest.TestCase):
    def test_accepts_a_plain_playwright_test(self):
        check_code("from playwright.sync_api import expect\n\ndef test_repro(page, base_url):\n    page.goto(base_url)\n")

    def test_rejects_os_access_and_missing_entry_point(self):
        for code in ("import os\ndef test_repro(page, base_url): pass\n",
                     "def test_repro(page, base_url):\n    open('x')\n",
                     "def something_else(page): pass\n"):
            with self.assertRaises(UnsafeCode):
                check_code(code)


class EventsTest(unittest.TestCase):
    def test_stage_emits_started_and_done_in_order(self):
        bus = EventBus(run_id="test")
        seen = []
        bus.subscribe(seen.append)
        with bus.stage("triage", model="m") as st:
            st.progress("half way")
        self.assertEqual([(e.stage, e.status) for e in seen],
                         [("triage", "started"), ("triage", "progress"), ("triage", "done")])
        self.assertEqual([e.seq for e in seen], [0, 1, 2])
        self.assertTrue(seen[0].sse().startswith("id: 0\nevent: triage\ndata: {"))

    def test_stage_reports_failure(self):
        bus = EventBus(run_id="test")
        with self.assertRaises(RuntimeError):
            with bus.stage("fix"):
                raise RuntimeError("boom")
        self.assertEqual(bus.events[-1].status, "failed")


class ShipAndSourceTest(unittest.TestCase):
    def test_e2e_file_is_self_contained(self):
        path, content = e2e_test_file("def test_repro(page, base_url):\n    assert re.match('a', 'a')\n",
                                      "Coupon stacks!", "bug.mp4")
        self.assertEqual(path, "e2e/test_coupon_stacks.py")
        self.assertIn("from playwright.sync_api import expect", content)
        self.assertIn("import re", content)
        self.assertEqual(slugify("  Hello, World  "), "hello-world")

    def test_repo_map_and_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.js").write_text('export function addItem() {}\n'
                                         'const row = `<li data-testid="row-${id}">`;\n', encoding="utf-8")
            self.assertIn("defines: addItem", repo_map(root))
            self.assertIn('data-testid="row-${id}"', source_elements(root))


class ReplayStreamTest(unittest.TestCase):
    def test_replay_streams_every_event_then_ends(self):
        import asyncio
        import json

        from app.runs import RunStore, stream

        bus = EventBus(run_id="rec")
        with bus.stage("triage"):
            pass
        bus.emit("pipeline", "done", "ok")
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "rec.json"
            recording.write_text(json.dumps({"events": bus.dump()}), encoding="utf-8")
            run = RunStore().start_replay(recording, Path("video.mp4"), "video.mp4")

            async def collect():
                return [chunk async for chunk in stream(run)]

            chunks = asyncio.run(collect())
        self.assertEqual(sum(c.startswith("id: ") for c in chunks), 3)
        self.assertTrue(chunks[-1].startswith("event: end"))


if __name__ == "__main__":
    unittest.main()
