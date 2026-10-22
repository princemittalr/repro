"""List every model available on Nebius Token Factory and check our routing.

Usage (from the project root, with the venv active):
    python -m scripts.list_models

It prints the NVIDIA models first, then everything else (useful for spotting
an audio/speech model), and finally checks that each MODEL_* value in .env
is a real, live model ID.
"""

from __future__ import annotations

from app.config import settings
from app.llm import get_client

ENV_NAMES = {
    "perceive": "MODEL_PERCEIVE",
    "triage": "MODEL_TRIAGE",
    "coder": "MODEL_CODER",
    "escalate": "MODEL_ESCALATE",
}


def main() -> None:
    client = get_client()
    ids = sorted(m.id for m in client.models.list().data)
    nvidia = [i for i in ids if "nvidia" in i.lower() or "nemotron" in i.lower()]
    others = [i for i in ids if i not in nvidia]

    print(f"Token Factory returned {len(ids)} models.\n\nNVIDIA ({len(nvidia)}):")
    for model_id in nvidia:
        print(f"  - {model_id}")
    print(f"\nOther ({len(others)}):")
    for model_id in others:
        print(f"  - {model_id}")

    print("\nRouting check (.env values):")
    live = set(ids)
    for role, model_id in settings.models.as_dict().items():
        status = "OK     " if model_id in live else "MISSING"
        print(f"  {status} {ENV_NAMES[role]}={model_id}")


if __name__ == "__main__":
    main()
