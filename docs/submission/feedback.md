# Feedback on Nebius Token Factory, NVIDIA Nemotron and the NVIDIA tools (draft)

Written from building Repro during the hackathon. Facts below come from our own calls, logs and evals.

## What we used

| Product | Used for |
|---------|----------|
| Nebius Token Factory | Every model call, through the OpenAI-compatible API with the official `openai` Python SDK (`base_url` swap only). Guided JSON decoding (`response_format: json_schema`) for structured outputs; native function calling for the fix agent; `video_url` and `image_url` inputs for perception. |
| Nemotron 3.5 Super VL | Watching bug recordings: per-keyframe transcription (thinking off) and the final repro report (thinking on). |
| Nemotron 3.5 Lightning | Cheap triage (ranking files from a repo map) and JSON repair. |
| Nemotron 3 Super 120B | Writing Playwright tests from sandbox feedback, and the tool-calling fix agent. |
| Nemotron 3 Ultra 550B | Escalation when Super fails twice. |

## What worked well

- **Drop-in OpenAI compatibility.** Swapping `base_url` and the key was all it took; the official SDK,
  retries and timeouts worked unchanged.
- **Video input works.** A 10 to 14 s screen recording sent as a base64 `video_url` cost only about
  1.5k to 2.5k prompt tokens, far cheaper than the same clip as frames.
- **Function calling** was reliable on Super, Ultra and Lightning alike: correct tool names and valid
  JSON arguments on the first try in our probes.
- **Guided JSON decoding** gave syntactically valid output every time, including on reasoning models
  (the reasoning goes to `reasoning_content`, the JSON to `content`).
- **`chat_template_kwargs: {"enable_thinking": false}`** works and is a big lever: for pure
  transcription it cut latency and tokens sharply with no loss in quality.
- **A clear 402 error** (`credit_balance_exhausted`) when credits ran out, rather than a vague failure.
- The model lineup fits agent routing well: small and fast, mid-size coder, large escalation, VLM.

## What needs work

1. **Reasoning tokens count against `max_tokens`, with no separate budget.** At `max_tokens=4096` the
   VL model sometimes spent all 4096 tokens reasoning and returned `content: null` with
   `finish_reason: "length"`. We saw no effect from `reasoning_effort: "low"` or a
   `reasoning_budget` template kwarg; only `enable_thinking: false` changed behaviour. A real
   reasoning budget (or effort levels) would make cost and latency predictable.
2. **The 8-images-per-request limit**, with a video counting towards it, shaped our whole perception
   design: we transcribe 16 keyframes in two batches. A higher limit, or documenting it next to the
   model card, would help multimodal agents.
3. **Image cost visibility.** A 1024x576 keyframe costs about 585 prompt tokens. That's fine, but we
   only found out by measuring; per-image and per-second-of-video token costs in the docs would help
   planning.
4. **Image/label association.** With interleaved text labels and images, the VL model tied a label to
   the image *before* it, which shifted every frame by one until we wrote "Image n of N, follows:".
   A recommended prompt format for multi-image inputs would save others the same debugging.
5. **No Nemotron omni (audio + video) model** that we could find on Token Factory. Many bug recordings
   include spoken narration ("now I click remove…"), which would be a strong extra signal.
6. **Reasoning in small models.** Lightning also reasons by default; for JSON repair that cost us one
   truncated reply before we turned thinking off. Clear per-model defaults in the docs would help.
7. **Credits and spend visibility.** Our credits ran out mid-build, which stopped every model call at
   once. A spend alert, or a remaining-balance field in the API (or response headers), would let
   agents degrade gracefully instead of failing.
8. **Onboarding:** getting started was quick, but the model IDs differ in style
   (`nvidia/Nemotron-3_5-Lightning` vs `nvidia/nemotron-3-super-120b-a12b`), so we wrote a script to
   list models and check our routing. Consistent IDs, or aliases like `nemotron-super-latest`, would help.

## Would we build on it again?

Yes. The OpenAI compatibility, video input and reliable function calling let a hackathon project run
a real multi-model agent with one client. With a proper reasoning budget, a higher image limit and an
omni model, it would be an even better fit for multimodal coding agents.
