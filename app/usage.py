"""Token and cost accounting for every model call in a run.

Prices are USD per 1M tokens (input, output). Fill them in from the Token
Factory pricing page; until then cost shows as unknown rather than a guess.
"""

from __future__ import annotations

from app.schemas import LLMCall

PRICES_PER_M: dict[str, tuple[float, float]] = {
    # "nvidia/nemotron-3-super-120b-a12b": (0.00, 0.00),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    price = PRICES_PER_M.get(model)
    if price is None:
        return None
    return round((prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000, 6)


class UsageTracker:
    """Collects LLMCall records for one pipeline run."""

    def __init__(self) -> None:
        self.calls: list[LLMCall] = []

    def record(self, stage: str, model: str, usage, latency_s: float) -> LLMCall:
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        call = LLMCall(
            stage=stage, model=model, prompt_tokens=prompt, completion_tokens=completion,
            latency_s=round(latency_s, 2), cost_usd=estimate_cost(model, prompt, completion),
        )
        self.calls.append(call)
        return call

    def summary(self) -> dict:
        costs = [c.cost_usd for c in self.calls]
        return {
            "calls": [c.model_dump() for c in self.calls],
            "prompt_tokens": sum(c.prompt_tokens for c in self.calls),
            "completion_tokens": sum(c.completion_tokens for c in self.calls),
            "cost_usd": round(sum(costs), 6) if costs and all(c is not None for c in costs) else None,
        }
