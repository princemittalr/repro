"""Run the app under test (e.g. ShopLite) on a free local port.

The app is copied into a throwaway folder first, so generated tests and later
code patches can never touch the original checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

IGNORED = shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".venv")


class TargetError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_command(app_dir: Path) -> list[str]:
    """Work out how to start the app. Node apps with an npm start script are supported today."""
    package = app_dir / "package.json"
    if package.exists() and "start" in json.loads(package.read_text(encoding="utf-8")).get("scripts", {}):
        npm = shutil.which("npm")
        if not npm:
            raise TargetError("npm is not installed or not on PATH.")
        return [npm, "start"]
    raise TargetError(f"Don't know how to start the app in {app_dir} (no npm start script).")


def _install_dependencies(app_dir: Path) -> None:
    package = app_dir / "package.json"
    deps = json.loads(package.read_text(encoding="utf-8")).get("dependencies", {}) if package.exists() else {}
    if deps:
        npm = shutil.which("npm")
        cmd = [npm, "ci"] if (app_dir / "package-lock.json").exists() else [npm, "install"]
        subprocess.run(cmd, cwd=app_dir, check=True, capture_output=True)


def _wait_until_up(url: str, process: subprocess.Popen, timeout_s: float = 20) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            out = process.stdout.read() if process.stdout else ""
            raise TargetError(f"The app exited during start-up:\n{out[-1000:]}")
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status < 500:
                    return
        except OSError:
            time.sleep(0.25)
    raise TargetError(f"The app did not answer at {url} within {timeout_s:.0f}s.")


@contextmanager
def running_app(source_dir: Path, workdir: Path | None = None) -> Iterator[tuple[str, Path]]:
    """Copy `source_dir`, start it, and yield (base_url, copied_dir). Stops it on exit."""
    with tempfile.TemporaryDirectory() as tmp:
        app_dir = (workdir or Path(tmp)) / "app"
        if not app_dir.exists():
            shutil.copytree(source_dir, app_dir, ignore=IGNORED)
        _install_dependencies(app_dir)

        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        process = subprocess.Popen(
            _start_command(app_dir), cwd=app_dir, env={**os.environ, "PORT": str(port)},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=(os.name != "nt"),  # own process group, so we can stop npm's children too
        )
        try:
            _wait_until_up(base_url, process)
            yield base_url, app_dir
        finally:
            _stop(process)


def _stop(process: subprocess.Popen) -> None:
    """Stop the server and any child it spawned (npm start runs node as a child)."""
    if process.poll() is not None:
        return
    if os.name == "nt":  # Windows: kill the whole process tree
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
