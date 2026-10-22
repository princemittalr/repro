# Repro

**Show it the bug. It ships the fix.**

Repro watches a screen recording of a bug, writes a Playwright test that fails because of it, fixes
the code until that test passes (without breaking the existing suite), and opens a GitHub pull
request with the fix, the new regression test and the red/green evidence. No human writes any of it.

Built for the **Nebius x NVIDIA Global AI Hackathon** (Coding and Agentic Engineering track) on
**NVIDIA Nemotron** models served by **Nebius Token Factory**, with live web research by **Tavily**.

![Repro UI: video on the left, live agent timeline on the right](docs/screenshot.png)
<!-- TODO: replace with a GIF of a live run (start screen shown for now). -->

## Why

Bug reports are often just a video: "it does this, see attached". Turning that into a fix means
watching it, working out the exact steps, reproducing it locally, writing a test, finding the root
cause, fixing it and proving the fix. Repro does that whole loop, and every claim it makes in the
pull request is backed by a test that went from red to green.

## How it works

```mermaid
flowchart LR
    V[Screen recording] --> P[PERCEIVE<br/>Nemotron 3.5 Super VL]
    P -->|repro steps + timestamps| T[TRIAGE<br/>Nemotron 3.5 Lightning]
    T -->|ranked files| R[REPRODUCE<br/>Nemotron 3 Super]
    R -->|red Playwright test| F[FIX agent<br/>Nemotron 3 Super]
    F <-->|web_search| S[RESEARCH<br/>Tavily]
    F -.->|failed twice| U[ESCALATE<br/>Nemotron 3 Ultra]
    U --> F
    F -->|patched code| C[VERIFY<br/>regression test + npm test]
    C -->|green| G[SHIP<br/>GitHub pull request]
    subgraph Sandbox
        R
        C
    end
```

1. **Perceive.** ffmpeg picks keyframes just *before* and just *after* every visible change. The
   vision model transcribes each one (pointer position, every value on screen); a deterministic diff
   turns those transcripts into a timeline ("Logo Cap row disappeared, triggered on: Remove link in
   the Logo Cap row"). The model then writes a structured `ReproReport` from the timeline, the
   keyframes and the video.
2. **Triage.** The fast Lightning model ranks the files to read first from a compact repository map.
3. **Reproduce.** Nemotron 3 Super writes a Python Playwright test from the report, the start
   page's accessibility tree and the app's element templates. It runs in a sandbox (AST safety
   check, separate process, timeouts) and is classified *red* (proves the bug), *green* (misses it)
   or *broken* (test bug), with feedback until it is red.
4. **Fix.** A tool-calling agent (`read_file`, `search_code`, `edit_file`, `run_tests`,
   `web_search`, ...) patches a throwaway copy of the app. It cannot edit existing tests, and
   `finish` is only accepted after a fully green run. After two failed runs Nemotron 3 Ultra takes
   over the same conversation.
5. **Verify.** The regression test and the existing suite run once more on the final code.
6. **Ship.** The fix and the regression test (under `e2e/`) are committed to a new branch through
   the GitHub API, and a pull request links every repro step to its timestamp in the video.

Every stage emits events (stage, model, status, tokens, latency, payload) that the web UI streams
live over Server-Sent Events.

## How Repro uses Nemotron, Nebius Token Factory and Tavily

All model calls go through Nebius Token Factory's OpenAI-compatible API (`app/llm.py`), with
guided JSON decoding for structured outputs and native function calling for the agent.

| Stage | Model | Why this model |
|-------|-------|----------------|
| Perceive | `nvidia/Nemotron-3.5-Super-VL` | Vision-language model that accepts both video and images. Used with thinking **off** for per-frame transcription (cheap reading) and **on** for the final report (reasoning over the timeline). |
| Triage | `nvidia/Nemotron-3_5-Lightning` | Fast and cheap, enough to rank files from a repo map; saves tool calls on the expensive coder. |
| Reproduce | `nvidia/nemotron-3-super-120b-a12b` | Strong coding model; writes the Playwright test and corrects it from sandbox feedback. |
| Fix | `nvidia/nemotron-3-super-120b-a12b` | Reliable function calling for a multi-step agent loop. |
| Escalate | `nvidia/Nemotron-3-Ultra-550b-a55b` | Largest model, used only when Super fails twice, so its cost is paid only on hard bugs. |
| Research | Tavily Search + Extract | Live, LLM-ready web results on error messages and library behaviour, called by the agent as a tool; every query and source is logged and cited in the PR. |

Token Factory limits we designed around: at most 8 images per request (keyframes are transcribed
in batches of 8), and reasoning tokens that count against `max_tokens` (each call has a budget and
retries with thinking off if the model never gets to the answer).

## Results

Measured on the three ShopLite sample recordings (`samples/`), against ground truth recorded by
the sample generator (`samples/ground_truth.json`, `samples/*.events.json`).

**Perceive** (`python -m scripts.eval_perception --modes hybrid --runs 2`)

| Mode | Step recall | Key facts correct | Tokens per video |
|------|-------------|-------------------|------------------|
| hybrid (video + keyframe timeline) | 100% | 6/6 | 21k to 26k |
| frames (keyframe timeline only) | 100% | 6/6 | 21k to 24k |
| video only | 58% | 2/6 | 8k to 16k |

The first version scored 2/6: the model credited a removal to the row that slid under the cursor
afterwards, dropped minus signs, and its hidden reasoning sometimes used the whole output budget.
Before/after keyframes, the deterministic timeline diff and explicit token budgets fixed that.

**Reproduce** (`python -m scripts.eval_reproduce`): a test counts only if it is **red on the buggy
app and green on a reference-fixed app** (`samples/reference/cart.fixed.js`).

| Bug | Red on buggy app | Green on fixed app | Attempts | Tokens (reproduce only) |
|-----|------------------|--------------------|----------|-------------------------|
| Coupon stacks | yes | yes | 1 (sometimes 2) | about 5k per attempt |
| Remove keeps the price | yes | yes | 1 | about 5k |
| Quantity goes negative | yes | in about half of the runs | 1 | about 5k |

The quantity bug's correct behaviour is ambiguous: "stop at 0", "remove at 0" and "clamp at 1" are
all reasonable fixes, and the reference clamps at 1. Tests that assert "stays at 0" are reported
as a reasonable alternative rather than forced to match.

**Full pipeline** (`python -m scripts.eval_pipeline`): fix and PR results for the coupon and remove
bugs are added here after the end-to-end evaluation run.

## Quick start

Requirements: Python 3.11+, Node.js 20+, ffmpeg, git.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env        # then fill in your keys (see docs/SETUP.md)
uvicorn app.main:app --reload # open http://127.0.0.1:8000
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env          # then fill in your keys (see docs/SETUP.md)
uvicorn app.main:app --reload # open http://127.0.0.1:8000
```

Install ffmpeg with `winget install Gyan.FFmpeg`, `brew install ffmpeg` or `apt install ffmpeg`.

**Docker**

```bash
docker build -t repro .
docker run --env-file .env -p 8000:8000 repro
```

### Command line

```bash
python -m scripts.run_pipeline samples/shoplite-coupon.mp4            # video in, PR out
python -m scripts.run_pipeline samples/shoplite-remove.mp4 --no-pr    # stop after verify
python -m scripts.perceive samples/shoplite-coupon.mp4                # repro steps only
python -m scripts.reproduce samples/shoplite-coupon.mp4               # red Playwright test only
python -m scripts.eval_perception --modes hybrid --runs 2
python -m scripts.eval_reproduce
python -m scripts.eval_pipeline
python -m unittest discover tests                                     # offline unit tests
```

## Project layout

| Path | What it is |
|------|------------|
| `app/perception.py`, `app/video.py` | PERCEIVE: keyframes, transcription, timeline diff, report |
| `app/triage.py` | TRIAGE: repo map and file ranking |
| `app/reproduce.py`, `app/sandbox.py`, `app/harness.py` | REPRODUCE: test writing, safety check, sandboxed runner |
| `app/fix.py`, `app/research.py`, `app/checks.py` | FIX agent, Tavily research, regression + suite checks |
| `app/ship.py` | SHIP: branch, commit and pull request via the GitHub API |
| `app/pipeline.py`, `app/events.py` | Stage orchestration and SSE-ready events |
| `app/main.py`, `app/runs.py`, `app/static/` | Web UI and API |
| `shoplite/` | The demo app with three deliberate bugs ([its own repo](https://github.com/princemittalr/shoplite)) |
| `samples/` | Sample recordings and ground truth for the evals |
| `demo/` | Recorded real runs for the UI's instant Replay |

## Limitations

- Target apps must start with `npm start` (Node). Other stacks need a start command in `app/target.py`.
- Regression tests are Python Playwright; the fix agent's unit tests follow the app's own framework.
- Perception is tuned on short (under a minute), single-page recordings; long, multi-page sessions
  are sampled more coarsely.
- When a bug's correct behaviour is ambiguous, the test may encode one reasonable fix (see the
  quantity bug above).
- Generated code runs in a sandboxed subprocess with an AST allow-list and timeouts; for untrusted
  repositories, run Repro itself in a container (see the Dockerfile).
- Cost per run is shown in tokens; dollar estimates need Token Factory prices in `app/usage.py`.

## License

MIT, see [LICENSE](LICENSE).
