"""De-risk the hardest part early: can our perception model on Token Factory watch a video?

Usage:
    python -m scripts.perception_probe              # generates a 4-second test clip with ffmpeg
    python -m scripts.perception_probe my_clip.mp4  # or probe with your own recording

It tries three input strategies, from best to most conservative, and reports
which ones Token Factory accepts. Phase 1 will use the best one that passes.
  A. video_url (base64 mp4) + use_audio_in_video   -> video AND audio
  B. video_url (base64 mp4) only                   -> video, no audio
  C. frames sampled with ffmpeg, sent as images    -> fallback path
"""

from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import settings
from app.llm import get_client

QUESTION = (
    "Describe this screen recording in one short paragraph: what is on screen, "
    "in what order things change, and whether you hear any audio."
)


def make_test_clip(out: Path) -> None:
    """Build a font-free 4s clip: 2s red then 2s blue, with a 440 Hz tone."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=640x360:d=2:r=10",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
    ]
    subprocess.run(cmd, check=True)


def sample_frames(video: Path, workdir: Path, fps: float = 1.0) -> list[Path]:
    """Extract one JPEG per second (max 16) for the image fallback."""
    pattern = workdir / "frame_%03d.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={fps},scale=640:-2", "-frames:v", "16", str(pattern)],
        check=True,
    )
    return sorted(workdir.glob("frame_*.jpg"))


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def ask(client, content: list[dict], extra_body: dict | None = None) -> str:
    resp = client.chat.completions.create(
        model=settings.models.perceive,
        messages=[{"role": "user", "content": content}],
        max_tokens=1024,
        temperature=0,
        extra_body=extra_body or {},
    )
    return (resp.choices[0].message.content or "").strip()


def main() -> None:
    client = get_client()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        if len(sys.argv) > 1:
            video = Path(sys.argv[1])
        else:
            video = workdir / "probe.mp4"
            make_test_clip(video)
            print("Generated test clip: 2s red, then 2s blue, with a 440 Hz beep.\n")

        video_part = {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64(video)}"}}
        frames = sample_frames(video, workdir)
        frame_parts = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64(f)}"}} for f in frames
        ]

        strategies = {
            "A  video + audio": ([{"type": "text", "text": QUESTION}, video_part],
                                 {"mm_processor_kwargs": {"use_audio_in_video": True}}),
            "B  video only   ": ([{"type": "text", "text": QUESTION}, video_part], None),
            "C  frames       ": ([{"type": "text", "text": QUESTION + f" ({len(frames)} frames, 1 per second.)"},
                                  *frame_parts], None),
        }

        print(f"Model: {settings.models.perceive}\n")
        for name, (content, extra) in strategies.items():
            try:
                answer = ask(client, content, extra)
                print(f"PASS  {name}: {answer[:300]!r}\n")
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL  {name}: {type(exc).__name__}: {str(exc)[:300]}\n")

    print("Paste this whole output to your coach. Phase 1 will use the best passing strategy.")


if __name__ == "__main__":
    main()
