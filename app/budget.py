"""Spending guard for the public demo.

Every live run spends real Nebius Token Factory credit, and the demo is open
to anyone with the URL. The guard keeps a public deployment inside a fixed
free budget, and turns "out of budget" into a friendly message (with the
instant Replay as the alternative) instead of an error:

* at most LIVE_RUNS_PER_DAY live runs per day, LIVE_RUNS_PER_VISITOR per visitor;
* one live run at a time (others are asked to wait a minute);
* a token ceiling per run (stops a runaway agent loop) and per day;
* at most PRS_PER_DAY real pull requests opened on the demo repo per day.

Counters live in runs/budget.json and reset at midnight UTC. Limits are set
with environment variables (see .env.example). The guard is only enforced by
the web server; command-line scripts are run by the developer and are not limited.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import PROJECT_ROOT

STATE_FILE = PROJECT_ROOT / "runs" / "budget.json"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Limits:
    live_enabled: bool = os.getenv("LIVE_RUNS_ENABLED", "1") == "1"
    runs_per_day: int = _env_int("LIVE_RUNS_PER_DAY", 10)
    runs_per_visitor: int = _env_int("LIVE_RUNS_PER_VISITOR", 2)
    tokens_per_run: int = _env_int("RUN_TOKEN_BUDGET", 300_000)
    tokens_per_day: int = _env_int("DAILY_TOKEN_BUDGET", 1_500_000)
    prs_per_day: int = _env_int("PRS_PER_DAY", 5)


class BudgetExceeded(RuntimeError):
    """Raised before a model call that would go over the run or daily token budget."""


class BudgetGuard:
    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self.enforced = False  # switched on by the web server
        self._lock = threading.Lock()
        self._active_runs = 0
        self._state = self._load()

    # ------------------------------------------------------------ persistence

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _fresh(self) -> dict:
        return {"date": self._today(), "live_runs": 0, "tokens": 0, "prs": 0, "visitors": {}}

    def _load(self) -> dict:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return state if state.get("date") == self._today() else self._fresh()
        except (OSError, ValueError):
            return self._fresh()

    def _save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self._state), encoding="utf-8")

    def _roll_day(self) -> None:
        if self._state["date"] != self._today():
            self._state = self._fresh()

    # ------------------------------------------------------------ live runs

    def admit(self, visitor: str, open_pr: bool) -> tuple[bool, bool, str]:
        """Try to reserve a live run. Returns (allowed, open_pr_allowed, reason_if_refused)."""
        replay_hint = " Try Replay on a sample: it shows a real recorded run instantly."
        with self._lock:
            self._roll_day()
            s, lim = self._state, self.limits
            if not lim.live_enabled:
                return False, False, "Live runs are switched off on this demo." + replay_hint
            if self._active_runs >= 1:
                return False, False, "Another live run is in progress. Please try again in a minute." + replay_hint
            if s["live_runs"] >= lim.runs_per_day or s["tokens"] >= lim.tokens_per_day:
                return False, False, "Today's free live-run budget for this demo is used up." + replay_hint
            if s["visitors"].get(visitor, 0) >= lim.runs_per_visitor:
                return False, False, f"You've used your {lim.runs_per_visitor} live runs for today." + replay_hint

            pr_allowed = open_pr and s["prs"] < lim.prs_per_day
            s["live_runs"] += 1
            s["visitors"][visitor] = s["visitors"].get(visitor, 0) + 1
            if pr_allowed:
                s["prs"] += 1
            self._active_runs += 1
            self._save()
            return True, pr_allowed, ""

    def release(self) -> None:
        with self._lock:
            self._active_runs = max(0, self._active_runs - 1)

    # ------------------------------------------------------------ tokens

    def check(self, run_tokens: int) -> None:
        """Called before every model call while the guard is enforced."""
        if not self.enforced:
            return
        with self._lock:
            self._roll_day()
            if run_tokens >= self.limits.tokens_per_run:
                raise BudgetExceeded(f"This run reached its {self.limits.tokens_per_run:,}-token budget and was stopped.")
            if self._state["tokens"] >= self.limits.tokens_per_day:
                raise BudgetExceeded("Today's free token budget for this demo is used up. Replay a sample instead.")

    def spend(self, tokens: int) -> None:
        if not self.enforced or tokens <= 0:
            return
        with self._lock:
            self._roll_day()
            self._state["tokens"] += tokens
            self._save()

    def status(self) -> dict:
        with self._lock:
            self._roll_day()
            s, lim = self._state, self.limits
            return {
                "live_enabled": lim.live_enabled,
                "live_runs_left_today": max(0, lim.runs_per_day - s["live_runs"]) if lim.live_enabled else 0,
                "runs_per_visitor": lim.runs_per_visitor,
                "busy": self._active_runs > 0,
            }


budget = BudgetGuard()
