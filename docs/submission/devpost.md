# Devpost description (draft)

**Tagline:** Show it the bug. It ships the fix.

## Inspiration

A lot of bug reports are just a screen recording: "it does this, see the video". Every one of them
costs an engineer the same routine: watch it frame by frame, work out the exact steps, reproduce it,
write a test, find the root cause, fix it, prove the fix. We wanted an agent that does that whole
loop, and backs every claim with a test that went from red to green.

## What it does

Repro takes a screen recording of a bug in a web app and, autonomously:

1. **Perceives** it: exact repro steps with timestamps, and the values on screen
   (Nemotron 3.5 Super VL).
2. **Triages** the repository: which files to read first (Nemotron 3.5 Lightning).
3. **Reproduces** the bug as a Playwright test that fails on it, run in a sandbox (Nemotron 3 Super).
4. **Fixes** the code with a tool-using agent that reads, searches, edits and re-runs the tests, and
   researches the web with **Tavily** when it needs to. It escalates to Nemotron 3 Ultra if it fails
   twice.
5. **Verifies** the regression test and the app's existing suite on the final code.
6. **Ships** a GitHub pull request with the fix, the new regression test, every step linked to its
   timestamp in the video, the red/green evidence, the research sources and which model did what.

A web UI shows it live: the video on the left (click a step to jump to it), the agent timeline on the
right, streamed over Server-Sent Events, with the model, tokens and time for every stage.

## How we built it

- **Python + FastAPI** backend, **vanilla JS + Tailwind** UI, **Server-Sent Events** for the live timeline.
- **Nebius Token Factory** (OpenAI-compatible API) for every model call: guided JSON decoding for
  structured outputs, and native function calling for the fix agent.
- **Perception pipeline:** ffmpeg finds every visible change at 16 fps and keeps the frame just
  *before* and just *after* each one. The VLM transcribes each keyframe (pointer position, every value
  on screen) with thinking off; a deterministic diff turns those transcripts into a timeline that says
  exactly what changed and where the pointer was just before. The model then reasons over that
  timeline, the keyframes and the video.
- **Sandboxed reproduction:** generated tests pass an AST allow-list and run in a separate process
  with timeouts. Each run is classified red, green or broken, and the result goes back to the model.
- **Fix agent:** guarded file tools (it cannot edit existing tests, and `finish` is only accepted after
  a fully green run), a `run_tests` tool that restarts the patched app, and Tavily `web_search` /
  `read_url` tools.
- **Shipping** through the GitHub API (PyGithub): one commit with the fix and the e2e test, then the PR.
- **Evals** with ground truth for every stage: perception (step recall and key facts), reproduction
  (red on the buggy app **and** green on a reference fix), and the full pipeline.

## Challenges we ran into

- **The vision model credited the wrong row.** After "Remove" on the first cart row, the rows slide up
  and the cursor ends up over the next row, so the model reported the wrong item as removed. Fix:
  keyframes from just before each change, plus a timeline that credits every change to the pointer
  position in the frame before it.
- **Hidden reasoning ate the output budget.** Nemotron reasoning models count thinking tokens against
  `max_tokens`; at 4096 the answer was sometimes empty, and a text-only repair step then invented a
  report. Fix: explicit budgets, thinking off where the task is just reading, and a retry without
  thinking instead of a "repair from nothing".
- **Image labels were read one frame off.** A "[t=4.1s]" label between two images was tied to the
  image before it. Fix: labels that say "Image n of N, t=…, follows".
- **Minus signs:** "-1" next to a stepper's "−" button was read as "1"; adding the post-change
  keyframes to the reasoning call fixed it.
- **Elements that only exist later** (cart rows) aren't on the start page, so the first test guessed
  selectors. Fix: element templates extracted from the app's own source, and a page-state report of
  everything that appeared during a failed test.
- **Ambiguous "expected" behaviour:** for the quantity bug, "stop at 0", "remove at 0" and "clamp at 1"
  are all reasonable fixes. We tell the test writer to assert only what every reasonable fix
  guarantees, and report the remaining cases honestly.

## Accomplishments that we're proud of

- Perception went from 2/6 to **6/6 key facts and 100% step recall** on our samples, at about
  21k to 26k tokens per video.
- Tests are judged the strict way: a reproduction only counts if it is red on the buggy app **and**
  green on an independently fixed one.
- Every claim in the pull request is backed by evidence, from timestamps to test runs to research sources.

## What we learned

- With vision models, deterministic structure around the model (frame selection, a diff, explicit
  labels) beats prompt wording.
- Routing by task pays off: Lightning for cheap ranking, thinking off for transcription, Ultra only on
  escalation.
- Evals with ground truth from day one turned "it looks right" into numbers we could improve.

## What's next for Repro

- More stacks beyond `npm start` apps (Python and Java web apps, monorepos).
- Running inside CI as a GitHub App that answers issues that have a video attached.
- Longer, multi-page recordings, and using the recording's audio narration once an omni model is available.

## Built with

See [built_with.md](built_with.md).
