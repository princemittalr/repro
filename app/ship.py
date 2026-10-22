"""SHIP: open a pull request with the fix and the new regression test.

The target repository is cloned fresh before the pipeline starts, so the fix
is made against exactly what is on the default branch. Here the changed
files, plus the Playwright regression test under ``e2e/``, are committed to a
new branch through the GitHub API (PyGithub, authenticated with
GITHUB_TOKEN): no local git credentials are needed. The pull request
description links every repro step to its timestamp in the recording, shows
the red/green evidence and lists which model handled which stage.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from github import Auth, Github, InputGitTreeElement
from pydantic import BaseModel

from app.config import settings

E2E_DIR = "e2e"


class ShipError(RuntimeError):
    pass


class PullRequestInfo(BaseModel):
    number: int
    url: str
    branch: str
    files: list[str]


def clone(repo: str, dest: Path) -> Path:
    """Shallow-clone a public GitHub repository (no credentials needed to read it)."""
    git = shutil.which("git")
    if not git:
        raise ShipError("git is not installed or not on PATH.")
    proc = subprocess.run([git, "clone", "--depth", "1", f"https://github.com/{repo}.git", str(dest)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ShipError(f"Could not clone {repo}: {proc.stderr.strip()[-300:]}")
    return dest


def slugify(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "bug"


def e2e_test_file(test_code: str, title: str, video_name: str) -> tuple[str, str]:
    """Path and content of the regression test as it ships in the PR (runnable with pytest-playwright)."""
    slug = slugify(title).replace("-", "_")
    header = (f'"""Regression test for: {title}\n\nWritten by Repro from the screen recording {video_name}. '
              "It failed before the fix and passes after it.\n\nRun it against a running app:\n"
              "    pip install pytest-playwright && playwright install chromium\n"
              f'    pytest {E2E_DIR} --base-url http://localhost:3000\n"""\n\n')
    code = test_code if "from playwright.sync_api import expect" in test_code else (
        "from playwright.sync_api import expect\n" + ("import re\n" if "re." in test_code else "") + "\n" + test_code)
    return f"{E2E_DIR}/test_{slug}.py", header + code


def open_pull_request(repo_name: str, files: dict[str, str], title: str, body: str, branch_hint: str) -> PullRequestInfo:
    """Commit `files` ({path: content}) on a new branch off the default branch and open a PR."""
    if not settings.github_token or "your" in settings.github_token:
        raise ShipError("GITHUB_TOKEN is missing; add it to .env to open pull requests.")
    repo = Github(auth=Auth.Token(settings.github_token)).get_repo(repo_name)
    base = repo.get_branch(repo.default_branch)
    branch = f"repro/{slugify(branch_hint, 32)}-{time.strftime('%Y%m%d-%H%M%S')}"

    # One commit with every file, built through the Git Data API.
    elements = [InputGitTreeElement(path=path, mode="100644", type="blob",
                                    sha=repo.create_git_blob(content, "utf-8").sha)
                for path, content in sorted(files.items())]
    base_commit = repo.get_git_commit(base.commit.sha)
    tree = repo.create_git_tree(elements, base_commit.tree)
    commit = repo.create_git_commit(f"Fix: {title}\n\nFound, reproduced and fixed by Repro.", tree, [base_commit])
    repo.create_git_ref(f"refs/heads/{branch}", commit.sha)

    pr = repo.create_pull(title=f"Fix: {title}", body=body, head=branch, base=repo.default_branch)
    return PullRequestInfo(number=pr.number, url=pr.html_url, branch=branch, files=sorted(files))


def close_pull_request(repo_name: str, number: int, comment: str | None = None) -> None:
    """Close a PR opened while testing (its branch is kept)."""
    repo = Github(auth=Auth.Token(settings.github_token)).get_repo(repo_name)
    pr = repo.get_pull(number)
    if comment:
        pr.create_issue_comment(comment)
    pr.edit(state="closed")
