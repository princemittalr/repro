"""ffmpeg helpers: inspect, normalise and sample frames from screen recordings."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class VideoError(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise VideoError(f"{tool} is not installed or not on PATH.")
    return path


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoError(result.stderr.strip()[-500:] or f"{args[0]} failed")
    return result.stdout


def duration_seconds(video: Path) -> float:
    out = _run([_require("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)])
    return float(json.loads(out)["format"]["duration"])


def normalize(video: Path, out: Path, width: int = 1280, fps: int = 10) -> Path:
    """Re-encode any upload (webm, mov, mkv...) to a compact silent H.264 MP4.

    Screen recordings are mostly static, so a low frame rate and high CRF keep
    the payload small without losing readable text.
    """
    _run([
        _require("ffmpeg"), "-y", "-loglevel", "error", "-i", str(video),
        "-vf", f"scale='min({width},iw)':-2,fps={fps}", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "30", "-movflags", "+faststart", str(out),
    ])
    return out


@dataclass(frozen=True)
class Frame:
    timestamp_s: float
    path: Path


def _frame_at(video: Path, timestamp_s: float, out: Path, width: int) -> Path:
    _run([
        _require("ffmpeg"), "-y", "-loglevel", "error", "-ss", f"{timestamp_s:.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", str(out),
    ])
    return out


def _first_content_frame(video: Path, fps: float, size: tuple[int, int] = (160, 90)) -> int:
    """Index of the first non-blank frame, since recordings often open on an
    empty screen while the page loads. Decoding tiny raw grayscale frames
    through ffmpeg keeps us free of image libraries."""
    w, h = size
    raw = subprocess.run(
        [_require("ffmpeg"), "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    frame_size = w * h
    frames = [raw[i : i + frame_size] for i in range(0, len(raw) - frame_size + 1, frame_size)]
    # A loading screen has only a couple of flat tones; real UI has many.
    return next((i for i, f in enumerate(frames) if len({v // 16 for v in f}) > 3), 0)


def _changed_pixels(video: Path, fps: float, width: int = 1280) -> list[int]:
    """Number of pixels that changed since the previous frame, per frame.

    Measured at (nearly) full resolution so a single digit flipping, like a
    quantity going from -1 to -2, still registers. ffmpeg does the work:
    difference of consecutive frames, thresholded to 0/255, averaged.
    """
    out = _run([
        _require("ffmpeg"), "-loglevel", "error", "-i", str(video), "-vf",
        f"fps={fps},scale='min({width},iw)':-2,format=gray,tblend=all_mode=difference,"
        "lut=y='if(gt(val,24),255,0)',signalstats,"
        "metadata=print:key=lavfi.signalstats.YAVG:file=-",
        "-f", "null", "-",
    ])
    probe = _run([_require("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "stream=width,height", "-of", "json", str(video)])
    stream = json.loads(probe)["streams"][0]
    w = min(width, stream["width"])
    pixels = w * stream["height"] * w / stream["width"]  # frame area at the analysed scale
    averages = [float(line.split("=")[1]) for line in out.splitlines() if "YAVG=" in line]
    # tblend emits nothing for the very first frame: it has no predecessor.
    return [0] + [round(avg / 255 * pixels) for avg in averages]


@dataclass(frozen=True)
class _Change:
    start: int  # first frame index that differs
    end: int    # last frame index that differs
    peak: int   # frame index with the largest difference: the UI update itself
    size: int   # total changed pixels


def _changes(diffs: list[int], first: int, noise: int, max_gap: int) -> list[_Change]:
    """Group changed frames into separate visual changes (a click, a keystroke burst)."""
    changes: list[_Change] = []
    for i, d in enumerate(diffs):
        if i <= first or d <= noise:
            continue
        if changes and i - changes[-1].end <= max_gap:
            last = changes[-1]
            peak = i if d > diffs[last.peak] else last.peak
            changes[-1] = _Change(last.start, i, peak, last.size + d)
        else:
            changes.append(_Change(i, i, i, d))
    return changes


def sample_keyframes(video: Path, out_dir: Path, max_frames: int = 16, width: int = 1024) -> list[Frame]:
    """Pick up to `max_frames` stills that show every visible change.

    For each change we keep the frame just BEFORE its peak (the cursor already
    sits on the element being clicked, but the layout has not shifted yet)
    and the frame once it has SETTLED (the result). With both, the model can
    tell which row was clicked even when rows move up after a removal. The
    first and last frames are always kept; if there are too many changes, the
    smallest ones (usually pure cursor motion) are dropped first.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # A fine time grid puts the "before" frame within ~60 ms of the change.
    fps = 16.0 if duration_seconds(video) <= 60 else 4.0  # keep the analysis fast on long clips
    diffs = _changed_pixels(video, fps)
    first = _first_content_frame(video, fps)
    last = len(diffs) - 1
    changes = _changes(diffs, first, noise=20, max_gap=max(1, round(fps * 0.125)))

    def frames_for(k: int) -> set[int]:
        change = changes[k]
        settle_limit = changes[k + 1].start - 1 if k + 1 < len(changes) else last
        return {change.peak - 1, min(change.end + 2, settle_limit, last)}

    chosen = {first, last}
    for k in sorted(range(len(changes)), key=lambda k: changes[k].size, reverse=True):
        extra = frames_for(k) - chosen
        if len(chosen) + len(extra) > max_frames:
            continue
        chosen |= extra

    frames = []
    for n, idx in enumerate(sorted(chosen)):
        t = round(idx / fps, 2)
        frames.append(Frame(timestamp_s=t, path=_frame_at(video, t, out_dir / f"key_{n:02d}.jpg", width)))
    return frames
