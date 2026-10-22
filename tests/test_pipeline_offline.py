"""Full pipeline run with every model call stubbed: checks orchestration, events and the PR text.

TRIAGE and REPRODUCE return canned results and the fix agent follows a short
script, so no API is called. VERIFY really starts ShopLite and runs the
Playwright test and `npm test`. Needs Node.js and Playwright's Chromium.

    python -m unittest tests.test_pipeline_offline
"""

from __future__ import annotations

import shutil
import unittest
from unittest import mock

from app import fix as fix_module
from app import pipeline
from app.config import PROJECT_ROOT
from app.events import EventBus
from app.schemas import ReproAttempt, ReproReport, ReproResult, ReproStep, TestRun
from app.triage import RankedFile, Triage
from tests.test_fix_agent import CORRECT_FIX, REMOVE_LINE, REMOVE_TEST, call, turn

REPORT = ReproReport(
    title="Cart totals do not update after removing an item", page="Cart",
    steps=[ReproStep(index=1, action="click", target="Add to cart (Logo Cap card)", timestamp_s=1.8),
           ReproStep(index=2, action="click", target="Remove (Logo Cap row)", timestamp_s=7.2)],
    expected_behavior="Subtotal drops by $24.00 to $17.49.", actual_behavior="Subtotal stays $41.49.",
    error_text=["Subtotal: $41.49"], confidence=0.9,
)


@unittest.skipUnless(shutil.which("npm"), "needs Node.js")
class OfflinePipelineTest(unittest.TestCase):
    def test_runs_every_stage_and_builds_the_pr_text(self):
        red = TestRun(outcome="failed", message="AssertionError: Locator expected to have text '$17.49'")
        repro = ReproResult(reproduced=True, test_code=REMOVE_TEST, attempts=[
            ReproAttempt(attempt=1, model="nvidia/nemotron-3-super-120b-a12b", code=REMOVE_TEST, run=red,
                         status="red", diagnosis="The bug is reproduced")])
        script = iter([
            turn(call("edit_file", path="public/cart.js", old_text=REMOVE_LINE, new_text=CORRECT_FIX)),
            turn(call("run_tests")),
            turn(call("finish", root_cause="removeItem left the line in the running subtotal.",
                      summary="Subtract the line before removing it.")),
        ])
        bus = EventBus(run_id="offline")
        with mock.patch.object(pipeline, "triage", return_value=Triage(
                files=[RankedFile(path="public/cart.js", reason="cart logic")], hypothesis="subtotal not reduced")), \
             mock.patch.object(pipeline, "reproduce", return_value=repro), \
             mock.patch.object(fix_module, "chat_tools", lambda **_: next(script)):
            result = pipeline.run_pipeline(PROJECT_ROOT / "samples" / "shoplite-remove.mp4",
                                           app_dir=PROJECT_ROOT / "shoplite", open_pr=False, report=REPORT, bus=bus)

        self.assertTrue(result.success)
        self.assertTrue(result.verify.green)
        finals = [(e.stage, e.status) for e in bus.events if e.status != "progress" and e.status != "started"]
        self.assertEqual(finals, [("perceive", "done"), ("triage", "done"), ("reproduce", "done"), ("fix", "done"),
                                  ("research", "skipped"), ("verify", "done"), ("ship", "skipped"),
                                  ("pipeline", "done")])

        body = pipeline.pr_body(result, bus, "shoplite-remove.mp4", red.message)
        self.assertIn("| 2 | 0:07.2 | click | Remove (Logo Cap row) |", body)
        self.assertIn("existing suite (`npm test`) passed", body)
        self.assertIn("`public/cart.js`", body)


if __name__ == "__main__":
    unittest.main()
