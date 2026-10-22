"""PERCEIVE stage: turn a screen recording into structured repro steps.

The vision-language model (Nemotron 3.5 Super VL) watches the recording and
returns a `ReproReport`. It works like a careful human tester, in three passes:

1. **Transcribe**: sharp keyframes, taken just before and just after every
   visible change, are read one by one into plain text: where the pointer
   is, whether a click ring shows, and every changeable value on screen.
   Thinking is off here: it is reading, not reasoning, so it is cheap.
2. **Diff** (no model): consecutive transcripts are compared line by line,
   giving an exact timeline of what appeared, disappeared or changed value,
   and where the pointer was just before. This is what tells "the row I
   clicked vanished" apart from "the row now under the cursor".
3. **Report**: the model reasons over the timeline, the keyframes right
   after each change (to double-check small details like minus signs) and,
   in ``hybrid`` mode, the clip itself for motion and order.

An optional text-only **verify** pass re-checks the draft against the
timeline. It is off by default: once the draft is grounded in the timeline
it adds ~7k tokens and, in our evals, made reports worse, not better.

Modes:
   * ``hybrid`` (default): video + keyframe timeline + keyframes.
   * ``frames``: keyframe timeline + keyframes, no video.
   * ``video``: the whole clip only. Cheapest, least precise; no timeline.

Output is constrained to the `ReproReport` JSON schema with guided decoding.
If a reply is cut off before answering, the call is retried with thinking
off; if a reply fails validation, the fast triage model repairs that reply
(it never writes a report from scratch).
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.llm import TruncatedReply, chat_json
from app.schemas import ReproReport
from app.usage import UsageTracker
from app.video import Frame, duration_seconds, normalize, sample_keyframes

Mode = Literal["hybrid", "video", "frames"]

MAX_IMAGES_PER_REQUEST = 8  # Token Factory limit; a video counts towards it
MAX_KEYFRAMES = 16          # transcribed in batches of MAX_IMAGES_PER_REQUEST
REASONING_MAX_TOKENS = 12000  # thinking + answer; the report alone is ~1-2k tokens

RULES = """Rules:
- List every user action in order: navigate, click, type, select, scroll, hover, wait, or observe.
- "target" must be the exact visible text or label of the element (button text, input placeholder,
  link text), plus the card or row it belongs to, e.g. "Add to cart (Classic Tee card)".
- "timestamp_s" is when the action happens, in seconds from the start of the video.
- Every click is one step: never merge or skip repeated clicks on the same button. A click ring
  seen in two consecutive frames on the same element, with one change between them, is one click.
- When a field's text changes, that is a "type" step with the new text as "value"; the click that
  focused the field is part of it, not a separate step.
- A click lands on the element under the pointer in the frame just BEFORE the change it causes.
  Lists can shift after the change, so in the frame after, the pointer may sit on a different row.
- To tell which item was removed or changed, use the rows that disappeared or whose values changed.
- Quote numbers exactly as displayed, including minus signs. Only mention discounts, coupons or
  messages that are actually visible.
- Compare what a user would reasonably expect with what actually appears on screen, and compute the
  expected values from the prices or inputs shown. Quote exact wrong values in "error_text".
- Do not invent steps you cannot see. Lower "confidence" if the recording is ambiguous."""

TRANSCRIBE_PROMPT = """You transcribe keyframes of a screen recording of a web app for a QA engineer who cannot
see them. Each image comes right AFTER its label "Image n of N, t=...s". For EACH image, in order,
write one entry:
- "t": the timestamp from that image's label.
- "pointer": the element under the mouse pointer (in these recordings it may be drawn as a red dot),
  as its visible label plus the row, card or section it is in, e.g. "Delete link in the 'Invoice 1042'
  row". Use the row the pointer is vertically level with. Write "none" if no pointer is visible.
- "click": true only if a click ring or ripple is visible around the pointer.
- "state": every piece of content a user action could change, one short line each, written as
  "Section > item: values". Include every row of every list, cart or table with all its values
  (name, quantity, price, status), every form field with its current text (or "empty"), totals,
  counters, messages, banners and errors. Skip fixed page chrome such as the logo and navigation.
Copy text and numbers exactly as shown, character by character. Look closely for minus signs, even
in unusual places ("-3", "$-4.50"): a negative value is often the bug. For steppers such as
"[−] 2 [+]", report the number between the buttons; it can itself be negative ("[−] -1 [+]" is -1).
Use exactly the same wording for the same item in every frame, so frames can be compared line by line."""

REPORT_INTRO = """You are a senior QA engineer. You watch screen recordings of bugs in web apps
and write precise reproduction steps that another engineer can turn into an automated browser test."""

TIMELINE_NOTE = """You get a TIMELINE built from sharp keyframes: each frame lists where the pointer is, and what
changed on screen since the previous frame (computed exactly, line by line). For each CHANGE it
names the element the pointer was on just before it: that is the element the user acted on, even
if a later frame or image shows the pointer or a click ring over another row. Then come the sharp
keyframes right after the changes. The timeline is a machine transcript and can miss small details
such as a minus sign: when an image shows a value differently, trust the image."""


def _report_prompt(with_timeline: bool) -> str:
    note = f"\n\n{TIMELINE_NOTE}" if with_timeline else ""
    return f"{REPORT_INTRO}{note}\n\n{RULES}\n\nReply with ONE JSON object matching the ReproReport schema."

VERIFY_PROMPT = f"""You are a meticulous QA reviewer. Below is a DRAFT bug report written from a
screen recording, followed by the TIMELINE of keyframes from that recording (pointer position and
the exact on-screen changes between frames).

Check the draft against the timeline, one item at a time:
1. For each step: is there a change in the timeline that it explains, at about that time? Is its
   target the element under the pointer in the frame just before that change? Fix the target or
   timestamp if not. Remove steps with no evidence; add actions that a change shows but are missing.
2. For each number or message in actual_behavior, expected_behavior and error_text: does it appear
   exactly like that in the timeline? Replace it with the value actually shown, or delete it.
3. Recompute expected_behavior from the values on screen, so it states the correct values.

{RULES}

Reply with the corrected report as ONE JSON object matching the ReproReport schema."""


class PerceptionError(RuntimeError):
    pass


class FrameState(BaseModel):
    """What one keyframe shows, transcribed to text."""

    t: float = Field(description="Timestamp of the keyframe in seconds")
    pointer: str = Field(description="Element under the mouse pointer, with its row or card, or 'none'")
    click: bool = Field(description="True if a click ring is visible around the pointer")
    state: list[str] = Field(description="Every changeable value on screen, one 'Section > item: values' line each")


class Transcript(BaseModel):
    frames: list[FrameState]


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _frame_parts(frames: list[Frame]) -> list[dict]:
    # A bare "[t=...]" between two images is ambiguous, and the model tends to pin
    # it on the image before it, so every label says it is for the NEXT image.
    parts: list[dict] = []
    for n, frame in enumerate(frames, 1):
        parts.append({"type": "text", "text": f"Image {n} of {len(frames)}, t={frame.timestamp_s:.2f}s, follows:"})
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(frame.path)}"}})
    return parts


def _video_part(video: Path, workdir: Path) -> dict:
    small = normalize(video, workdir / "normalized.mp4")
    return {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{_b64(small)}"}}


def _transcribe(frames: list[Frame], tracker: UsageTracker) -> list[FrameState]:
    """Read every keyframe into a FrameState, MAX_IMAGES_PER_REQUEST frames per call."""
    states: list[FrameState] = []
    for i in range(0, len(frames), MAX_IMAGES_PER_REQUEST):
        batch = frames[i : i + MAX_IMAGES_PER_REQUEST]
        content = [{"type": "text", "text": TRANSCRIBE_PROMPT}]
        if states:
            # Later batches reuse the earlier wording, so the diff compares like with like.
            content.append({"type": "text", "text": "The frame before these was transcribed as follows; "
                                                    f"reuse its wording:\n{states[-1].model_dump_json()}"})
        content += [*_frame_parts(batch), {"type": "text", "text": f"Reply with JSON: {len(batch)} entries in \"frames\"."}]
        data = chat_json(stage="perceive:transcribe", model=settings.models.perceive, tracker=tracker,
                         messages=[{"role": "user", "content": content}], schema=Transcript.model_json_schema(),
                         schema_name="Transcript", max_tokens=6000, temperature=0, thinking=False)
        got = Transcript.model_validate(data).frames
        if len(got) != len(batch):
            # Rare: an image was skipped or doubled. Pair entries by their timestamp instead.
            by_t = {min(batch, key=lambda f: abs(f.timestamp_s - s.t)).timestamp_s: s for s in got}
            got = [by_t.get(f.timestamp_s) for f in batch]
        for frame, state in zip(batch, got):
            if state is None:  # no entry for this frame: carry the previous one forward ("no change")
                state = states[-1] if states else FrameState(t=0, pointer="none", click=False, state=[])
            # Trust our own timestamps over the model's copy of them.
            states.append(state.model_copy(update={"t": frame.timestamp_s}))
    return states


def _evidence(frames: list[Frame], states: list[FrameState], limit: int) -> list[Frame]:
    """The keyframes right after each change (the results), plus the last one, at most `limit`."""
    picked = [f for f, prev, cur in zip(frames[1:], states, states[1:])
              if {_norm(x) for x in prev.state} != {_norm(x) for x in cur.state}]
    if not picked or picked[-1] is not frames[-1]:
        picked.append(frames[-1])
    return picked[-limit:]


def _norm(line: str) -> str:
    return " ".join(line.lower().split())


def _key(line: str) -> str:
    """The 'Section > item' part of a state line, used to pair old and new values."""
    return _norm(line.split(":", 1)[0]) if ":" in line else _norm(line)


def timeline(states: list[FrameState]) -> str:
    """Deterministic, line-by-line diff of consecutive frame transcripts.

    Keyframes come in before/after pairs, so a change is credited to the
    pointer position in the frame BEFORE it. The frame after is not used for
    that: once a row is removed the others move up, and the pointer (and a
    lingering click ring) then sit over a different row than the one clicked.
    """
    out: list[str] = []
    prev: FrameState | None = None
    for s in states:
        if prev is None:
            out += [f"t={s.t:.2f}s  pointer: {s.pointer}", "  screen:", *(f"    {line}" for line in s.state)]
            prev = s
            continue
        before = {_norm(x) for x in prev.state}
        after = {_norm(x) for x in s.state}
        gone = [x for x in prev.state if _norm(x) not in after]
        new = [x for x in s.state if _norm(x) not in before]
        new_by_key = {_key(x): x for x in new}
        changes: list[str] = []
        for line in gone:
            match = new_by_key.pop(_key(line), None)
            if match is not None:
                changes.append(f"    changed: {line}  ->  {match.split(':', 1)[1].strip()}")
            else:
                changes.append(f"    disappeared: {line}")
        changes += [f"    appeared: {x}" for x in new if _key(x) in new_by_key]
        if changes:
            cause = prev.pointer if prev.pointer != "none" else s.pointer
            ring = ", click ring seen" if prev.click or s.click else ""
            if out and out[-1].startswith(f"t={prev.t:.2f}s  pointer:"):
                # The "before" frame is summed up in this change line; listing it on its own
                # too reads like a second click on the same element.
                out.pop()
            out += [f"t={s.t:.2f}s  CHANGE, triggered on: {cause} (pointer at t={prev.t:.2f}s{ring})", *changes]
        else:
            out.append(f"t={s.t:.2f}s  pointer: {s.pointer}{'  [click ring]' if s.click else ''}  (no change)")
        prev = s
    if prev is not None:
        out += [f"Final screen (t={prev.t:.2f}s):", *(f"    {line}" for line in prev.state)]
    return "\n".join(out)


def _ask(stage: str, model: str, content: list[dict], tracker: UsageTracker) -> ReproReport:
    """One structured call with reasoning: returns a validated report, repairing it if needed."""
    schema = ReproReport.model_json_schema()
    # Instructions go in the user turn: some vision chat templates ignore system prompts.
    messages = [{"role": "user", "content": content}]
    kwargs = dict(model=model, messages=messages, tracker=tracker, schema=schema, schema_name="ReproReport",
                  max_tokens=REASONING_MAX_TOKENS, temperature=0)
    try:
        try:
            data = chat_json(stage=stage, **kwargs)
        except TruncatedReply:
            data = chat_json(stage=f"{stage}:no-think", thinking=False, **kwargs)
        return ReproReport.model_validate(data)
    except (ValueError, ValidationError) as exc:
        return _repair(exc, tracker)


def _repair(error: Exception, tracker: UsageTracker) -> ReproReport:
    """Ask the fast triage model to fix malformed output without re-watching the video."""
    raw = str(getattr(error, "doc", "")) or str(error)
    if isinstance(error, TruncatedReply):
        # Nothing to repair: a text-only model would have to invent the whole report.
        raise PerceptionError(str(error)) from error
    try:
        data = chat_json(
            stage="perceive:repair",
            model=settings.models.triage,
            tracker=tracker,
            temperature=0,
            schema=ReproReport.model_json_schema(),
            schema_name="ReproReport",
            thinking=False,
            messages=[
                {"role": "system", "content": "You fix JSON so it validates against a schema. Keep every value "
                                              "from the broken output; never invent content. Reply with JSON only."},
                {"role": "user", "content": f"Schema:\n{json.dumps(ReproReport.model_json_schema())}\n\n"
                                            f"Error:\n{error}\n\nBroken output:\n{raw}"},
            ],
        )
        return ReproReport.model_validate(data)
    except (ValueError, ValidationError) as exc:
        raise PerceptionError(f"The model's report could not be parsed or repaired: {exc}") from exc


def perceive(
    video: Path,
    mode: Mode = "hybrid",
    tracker: UsageTracker | None = None,
    verify: bool = False,
    trace: dict | None = None,
) -> ReproReport:
    """Watch `video` and return validated repro steps.

    `verify` adds the text-only review pass (timeline modes only). Pass a
    dict as `trace` to receive the intermediate results (keyframe
    timestamps, timeline, the draft report) for debugging and evaluation.
    """
    tracker = tracker or UsageTracker()
    trace = {} if trace is None else trace
    duration = duration_seconds(video)
    model = settings.models.perceive

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        content = [{"type": "text", "text": _report_prompt(with_timeline=mode != "video")}]
        if mode in ("hybrid", "video"):
            content += [{"type": "text", "text": f"Screen recording, {duration:.1f}s long:"}, _video_part(video, workdir)]
        if mode == "video":
            text_timeline = None
        else:
            frames = sample_keyframes(video, workdir / "frames", max_frames=MAX_KEYFRAMES)
            trace["keyframes_s"] = [f.timestamp_s for f in frames]
            states = _transcribe(frames, tracker)
            text_timeline = timeline(states)
            trace["timeline"] = text_timeline
            evidence = _evidence(frames, states, MAX_IMAGES_PER_REQUEST - (mode == "hybrid"))
            content += [
                {"type": "text", "text": f"TIMELINE ({duration:.1f}s recording):\n{text_timeline}"},
                {"type": "text", "text": "KEYFRAMES right after the changes:"},
                *_frame_parts(evidence),
            ]

        draft = _ask(f"perceive:{mode}", model, content, tracker)
        trace["draft"] = draft
        if not verify or text_timeline is None:
            return draft

        verify_content = [
            {"type": "text", "text": VERIFY_PROMPT},
            {"type": "text", "text": f"DRAFT:\n{draft.model_dump_json(indent=1)}\n\nTIMELINE:\n{text_timeline}"},
        ]
        return _ask("perceive:verify", model, verify_content, tracker)
