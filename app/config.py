"""Central configuration for Repro.

All secrets come from environment variables (loaded from a local `.env` file
in development). Nothing sensitive is ever hard-coded here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class ModelRoster:
    """Which Nemotron model handles which stage of the pipeline."""

    perceive: str  # Watches the screen recording (vision-language model)
    triage: str    # Fast, cheap normalisation of steps and file ranking
    coder: str     # Writes the Playwright repro and the fix (tool-using agent)
    escalate: str  # Called only when the coder fails twice

    def as_dict(self) -> dict[str, str]:
        return {"perceive": self.perceive, "triage": self.triage, "coder": self.coder, "escalate": self.escalate}


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str
    nebius_base_url: str
    tavily_api_key: str
    github_token: str
    github_demo_repo: str
    models: ModelRoster


def load_settings() -> Settings:
    return Settings(
        nebius_api_key=_get("NEBIUS_API_KEY"),
        nebius_base_url=_get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/"),
        tavily_api_key=_get("TAVILY_API_KEY"),
        github_token=_get("GITHUB_TOKEN"),
        github_demo_repo=_get("GITHUB_DEMO_REPO"),
        models=ModelRoster(
            perceive=_get("MODEL_PERCEIVE", "nvidia/Nemotron-3.5-Super-VL"),
            triage=_get("MODEL_TRIAGE", "nvidia/Nemotron-3_5-Lightning"),
            coder=_get("MODEL_CODER", "nvidia/nemotron-3-super-120b-a12b"),
            escalate=_get("MODEL_ESCALATE", "nvidia/Nemotron-3-Ultra-550b-a55b"),
        ),
    )


settings = load_settings()
