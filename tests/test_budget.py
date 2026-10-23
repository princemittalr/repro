"""Offline tests for the demo spending guard (app/budget.py).

    python -m unittest tests.test_budget
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import budget as budget_module
from app.budget import BudgetExceeded, BudgetGuard, Limits


class BudgetGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(budget_module, "STATE_FILE", Path(self.tmp.name) / "budget.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def guard(self, **limits) -> BudgetGuard:
        g = BudgetGuard(Limits(**{"live_enabled": True, "runs_per_day": 3, "runs_per_visitor": 2,
                                  "tokens_per_run": 1000, "tokens_per_day": 5000, "prs_per_day": 1, **limits}))
        g.enforced = True
        return g

    def test_one_live_run_at_a_time(self):
        g = self.guard()
        self.assertTrue(g.admit("a", False)[0])
        allowed, _, reason = g.admit("b", False)
        self.assertFalse(allowed)
        self.assertIn("in progress", reason)
        g.release()
        self.assertTrue(g.admit("b", False)[0])

    def test_per_visitor_and_daily_limits(self):
        g = self.guard()
        for _ in range(2):
            self.assertTrue(g.admit("a", False)[0]); g.release()
        allowed, _, reason = g.admit("a", False)
        self.assertFalse(allowed)
        self.assertIn("your 2 live runs", reason)
        self.assertTrue(g.admit("b", False)[0]); g.release()
        allowed, _, reason = g.admit("c", False)
        self.assertIn("budget for this demo is used up", reason)
        self.assertIn("Replay", reason)

    def test_pull_requests_are_capped(self):
        g = self.guard()
        self.assertTrue(g.admit("a", True)[1]); g.release()
        allowed, pr_allowed, _ = g.admit("b", True)
        self.assertTrue(allowed)
        self.assertFalse(pr_allowed)

    def test_token_ceilings(self):
        g = self.guard()
        g.check(999)
        with self.assertRaises(BudgetExceeded):
            g.check(1000)
        g.spend(5000)
        with self.assertRaises(BudgetExceeded):
            g.check(0)
        allowed, _, _ = g.admit("a", False)
        self.assertFalse(allowed)

    def test_not_enforced_for_scripts(self):
        g = self.guard()
        g.enforced = False
        g.spend(10**9)
        g.check(10**9)  # no exception: CLI runs are the developer's call

    def test_counters_survive_a_restart(self):
        g = self.guard()
        g.admit("a", False); g.release()
        self.assertEqual(self.guard().status()["live_runs_left_today"], 2)

    def test_live_switch_off(self):
        allowed, _, reason = self.guard(live_enabled=False).admit("a", False)
        self.assertFalse(allowed)
        self.assertIn("switched off", reason)


if __name__ == "__main__":
    unittest.main()
