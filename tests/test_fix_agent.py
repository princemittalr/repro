"""Integration test of the FIX agent loop, with a scripted stand-in for the model.

No API calls: `chat_tools` is replaced by a script of tool calls. Everything
else is real: the workspace edits files on a copy of ShopLite, `run_tests`
starts the app and runs a Playwright regression test plus `npm test`, and the
agent escalates after two failed runs. Needs Node.js and Playwright's Chromium.

    python -m unittest tests.test_fix_agent
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import fix as fix_module
from app.config import PROJECT_ROOT, settings
from app.schemas import ReproReport
from app.target import IGNORED

REMOVE_TEST = '''def test_repro(page, base_url):
    page.goto(base_url)
    page.get_by_test_id("add-cap").click()
    page.get_by_test_id("add-stickers").click()
    page.get_by_test_id("add-mug").click()
    page.get_by_test_id("remove-cap").click()
    expect(page.get_by_test_id("subtotal")).to_have_text("$17.49")
'''

REMOVE_LINE = "  cart.items = cart.items.filter((item) => item.id !== productId);"
CORRECT_FIX = ("  const line = cart.items.find((item) => item.id === productId);\n"
               "  if (line) cart.subtotal -= line.price * line.quantity;\n" + REMOVE_LINE)
NO_OP_EDIT = "  // Remove the line for this product.\n" + REMOVE_LINE


def call(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id=f"call-{name}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def turn(*calls) -> SimpleNamespace:
    return SimpleNamespace(content="", tool_calls=list(calls))


@unittest.skipUnless(shutil.which("npm"), "needs Node.js")
class FixAgentLoopTest(unittest.TestCase):
    def test_escalates_after_two_failed_runs_then_fixes(self):
        script = [
            turn(call("read_file", path="public/cart.js")),
            turn(call("finish", root_cause="x", summary="x")),                                    # refused: not green
            turn(call("edit_file", path="public/cart.js", old_text=REMOVE_LINE, new_text=NO_OP_EDIT)),
            turn(call("run_tests")),                                                              # red #1
            turn(call("edit_file", path="test/cart.test.js", old_text="test(", new_text="test.skip(")),  # refused
            turn(call("run_tests")),                                                              # red #2 -> escalate
            turn(call("edit_file", path="public/cart.js", old_text=NO_OP_EDIT,
                      new_text=CORRECT_FIX.replace(REMOVE_LINE, NO_OP_EDIT))),
            turn(call("run_tests")),                                                              # green
            turn(call("finish", root_cause="removeItem never subtracted the line from the running subtotal.",
                      summary="Subtract the removed line's price x quantity before filtering it out.")),
        ]
        models: list[str] = []

        def fake_chat_tools(*, model, **_):
            models.append(model)
            return script[len(models) - 1]

        report = ReproReport(title="Remove keeps the price", page="Cart", steps=[], expected_behavior="",
                             actual_behavior="", confidence=1)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fix_module, "chat_tools", fake_chat_tools):
            app_dir = Path(tmp) / "app"
            shutil.copytree(PROJECT_ROOT / "shoplite", app_dir, ignore=IGNORED)
            result = fix_module.fix(report, REMOVE_TEST, "subtotal stayed $41.49", app_dir)

        self.assertTrue(result.fixed)
        self.assertTrue(result.escalated)
        self.assertEqual(models[:6], [settings.models.coder] * 6)
        self.assertEqual(set(models[6:]), {settings.models.escalate})
        refused = [s for s in result.steps if not s.ok]
        self.assertEqual([s.tool for s in refused], ["finish", "edit_file"])
        self.assertEqual(result.changed_files, ["public/cart.js"])
        self.assertIn("+  if (line) cart.subtotal -= line.price * line.quantity;", result.diff)
        self.assertTrue(result.checks.green)


if __name__ == "__main__":
    unittest.main()
