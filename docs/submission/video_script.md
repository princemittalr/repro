# Repro: 3-minute demo video script (draft)

Format: pitch. Problem, then the product working, then who it is for, then how it uses
Nebius Token Factory and NVIDIA Nemotron. Screen recording at 1920x1080 with voice-over; total 2:55.

Before recording:
- Start the server (`uvicorn app.main:app`) and open http://localhost:8000 (or the deployed URL).
- Do one full live run of the **coupon** sample with "Open a pull request" ticked. Keep its PR open
  in a browser tab, and copy the saved run to `demo/coupon.json` so Replay works as a backup.
- Close other tabs, hide bookmarks, zoom the browser to 110%.

| Time | Shot (what is on screen) | Narration |
|------|--------------------------|-----------|
| 0:00-0:12 | ShopLite in the browser. Add Classic Tee and Dev Mug, type SAVE10, Apply, Apply again: the discount doubles. | "This is a real bug report. No steps, no stack trace: just a screen recording that says *the coupon stacks*." |
| 0:12-0:25 | Slow zoom on the doubled discount (-$6.50). Cut to a GitHub issue that only has a video attached. | "Turning a video like this into a fix means watching it frame by frame, reproducing it, writing a test, finding the root cause and proving the fix. That is an afternoon of an engineer's time, for every report." |
| 0:25-0:35 | Repro start screen. Hover the three sample cards. | "Repro does the whole loop on its own. Show it the bug, and it ships the fix." |
| 0:35-0:50 | Drop `shoplite-coupon.mp4` on the drop zone (or click *Run live*). The run screen opens: video on the left, timeline on the right; Perceive is running. | "I drop in the recording. Nemotron 3.5 Super VL, a vision-language model on Nebius Token Factory, watches it." |
| 0:50-1:08 | Perceive turns green; the repro steps appear with timestamps. Click step 6, *Apply*: the video jumps to 0:11.9. | "It doesn't just describe the video. It produces exact repro steps, each tied to the moment in the recording, and it reads the values on screen: a 3.25 discount turning into 6.50." |
| 1:08-1:25 | Triage (Lightning) and Reproduce. Reproduce shows the red *red test* badge; open the *Regression test* tab. | "Nemotron 3.5 Lightning ranks the files to look at. Then Nemotron 3 Super writes a Playwright test from those steps and runs it in a sandbox. It fails. That red test is the proof the bug is real." |
| 1:25-1:50 | Fix running: open the *Agent log* tab, with tool calls streaming (`read_file public/cart.js`, `edit_file`, `run_tests`). Research shows a Tavily query with sources. | "Now a tool-using agent on Nemotron 3 Super fixes it. It reads the code, searches the web with Tavily when it needs outside knowledge, edits, and re-runs the tests until they pass. If it fails twice, Nemotron 3 Ultra takes over." |
| 1:50-2:05 | Verify turns green (*green test* badge). Open the *Fix diff* tab: the duplicate-coupon guard in `applyCoupon`. | "Verify runs the new test and the app's existing suite one more time. Green, and nothing else broke. This is the whole fix." |
| 2:05-2:25 | Click *Open pull request*. On GitHub: the PR description with the timestamped steps table, red/green evidence, research links and the model table; the Files tab shows `public/cart.js` and `e2e/test_*.py`. | "And it opens a pull request: the fix, the new regression test, every step linked to its timestamp, and which model did what. A reviewer can check it in a minute." |
| 2:25-2:40 | Back in Repro: the *Model routing and usage* card. | "Each stage runs on the Nemotron model that fits it, from the fast Lightning for triage up to Ultra only when it's needed. All through one OpenAI-compatible API on Nebius Token Factory, for about 60 thousand tokens per bug." |
| 2:40-2:55 | Title card: "Repro: show it the bug, it ships the fix", repo URL, "NVIDIA Nemotron · Nebius Token Factory · Tavily". | "Repro is for every team whose bug reports arrive as videos: QA, support, and open-source maintainers. Show it the bug. It ships the fix." |

Notes:
- Replace "about 60 thousand tokens" with the real total from the run's usage card.
- If a live run is slow while recording, record the live run once and cut the waiting; the timeline
  timings on screen stay real. Use **Replay** only as a backup, and don't call it live.
- Keep narration under about 420 words; read it once against a timer before recording.
