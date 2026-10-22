"""Send one short prompt to each configured Nemotron model and report the result.

Usage:
    python -m scripts.smoke_test

A PASS line means Token Factory answered with that model ID. Latency and token
counts are printed because Phase 4 shows them in the cost panel.
"""

from __future__ import annotations

import time

from app.config import settings
from app.llm import get_client

PROMPT = "Reply with exactly the words: Repro is online."


def main() -> int:
    client = get_client()
    failures = 0

    for role, model_id in settings.models.as_dict().items():
        start = time.perf_counter()
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": PROMPT}],
                max_tokens=512,  # reasoning models may "think" before answering
                temperature=0,
            )
            elapsed = time.perf_counter() - start
            text = (resp.choices[0].message.content or "").strip().replace("\n", " ")
            usage = resp.usage
            tokens = f"{usage.prompt_tokens} in / {usage.completion_tokens} out" if usage else "n/a"
            print(f"PASS  {role:<8} {model_id}\n      {elapsed:.1f}s, {tokens}, reply: {text[:120]!r}")
        except Exception as exc:  # noqa: BLE001 - we want to report every failure
            failures += 1
            print(f"FAIL  {role:<8} {model_id}\n      {type(exc).__name__}: {exc}")

    print("\nAll models responded." if failures == 0 else f"\n{failures} model(s) failed. Paste this output to your coach.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
