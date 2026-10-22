# Repro

**Show it the bug. It ships the fix.**

Repro watches a screen recording of a bug, writes a failing Playwright test that reproduces it,
fixes the code until the test passes, and opens a GitHub pull request, all autonomously.
Built for the Nebius x NVIDIA Global AI Hackathon (Coding and Agentic Engineering track).

> Status: Phase 1 (perception: screen recording to structured repro steps). The full README with
> architecture and usage lands in Phase 6.

## How the models are routed

| Stage | Model (via Nebius Token Factory) | Why |
|-------|----------------------------------|-----|
| Perceive | Nemotron 3.5 Super VL | Vision-language model that watches the recording and outputs structured steps |
| Triage | Nemotron 3.5 Lightning | Fast, cheap model built for high-volume agent work |
| Reproduce + Fix | Nemotron 3 Super 120B | Tool-using coding agent |
| Escalate | Nemotron 3 Ultra 550B | Only when Super fails twice |

Web research on error messages uses **Tavily**.

## Quick start (development)

```bash
python -m venv .venv
# activate: .venv\Scripts\activate (Windows) or source .venv/bin/activate (macOS/Linux)
pip install -r requirements.txt
cp .env.example .env            # then fill in your keys
python -m scripts.list_models   # lists models and checks .env routing
python -m scripts.smoke_test    # one call to each Nemotron model
python -m scripts.check_keys    # verifies Tavily + GitHub
python -m scripts.perception_probe  # can the vision model watch a video?
python -m scripts.record_sample     # record ShopLite bug videos into samples/
python -m scripts.perceive samples/shoplite-coupon.mp4   # video -> repro steps JSON
python -m scripts.eval_perception --modes hybrid video frames  # accuracy vs ground truth
uvicorn app.main:app --reload   # http://127.0.0.1:8000/api/health
```

The demo target app lives in [shoplite/](shoplite/) (it becomes its own repo in Phase 2).

See [docs/SETUP.md](docs/SETUP.md) for account and API key setup.

## License

MIT, see [LICENSE](LICENSE).
