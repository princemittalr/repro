"""Data models shared across the pipeline.

The perception model must return JSON that validates against `ReproReport`.
Keeping the contract in one place means the prompt, the parser and the UI
always agree on field names.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ActionType = Literal["navigate", "click", "type", "select", "scroll", "hover", "wait", "observe"]


class ReproStep(BaseModel):
    index: int = Field(description="1-based order of the step")
    action: ActionType
    target: str = Field(description="Visible text or label of the element acted on, e.g. 'Add to cart' under 'Classic Tee'")
    value: str | None = Field(default=None, description="Text typed or option selected, if any")
    timestamp_s: float = Field(description="Seconds from the start of the video when the step happens")
    observation: str | None = Field(default=None, description="What visibly changed right after the step")


class KeyMoment(BaseModel):
    timestamp_s: float
    description: str


class ReproReport(BaseModel):
    title: str = Field(description="One-line bug title, like a good issue title")
    app_url: str | None = Field(default=None, description="URL visible in the recording, if any")
    page: str = Field(description="Name of the page or screen where the bug happens")
    steps: list[ReproStep]
    expected_behavior: str
    actual_behavior: str
    error_text: list[str] = Field(default_factory=list, description="Exact error messages or wrong values visible on screen")
    key_moments: list[KeyMoment] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, description="How sure the model is that the steps reproduce the bug")


class LLMCall(BaseModel):
    """One model call, recorded for the routing and cost panel."""

    stage: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float | None = None
