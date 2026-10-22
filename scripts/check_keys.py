"""Verify the Tavily and GitHub credentials in .env actually work.

Usage:
    python -m scripts.check_keys
"""

from __future__ import annotations

from github import Auth, Github
from tavily import TavilyClient

from app.config import settings


def check_tavily() -> bool:
    try:
        result = TavilyClient(api_key=settings.tavily_api_key).search("Playwright strict mode violation", max_results=1)
        title = result["results"][0]["title"] if result.get("results") else "(no results)"
        print(f"PASS  Tavily search works. Top hit: {title}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  Tavily: {type(exc).__name__}: {exc}")
        return False


def check_github() -> bool:
    try:
        gh = Github(auth=Auth.Token(settings.github_token))
        login = gh.get_user().login
        print(f"PASS  GitHub token works. Logged in as: {login}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  GitHub token: {type(exc).__name__}: {exc}")
        return False

    try:
        repo = gh.get_repo(settings.github_demo_repo)
        print(f"PASS  Demo repo reachable: {repo.full_name}")
    except Exception as exc:  # noqa: BLE001
        # Expected until Phase 2, when we create the ShopLite repo.
        print(f"INFO  Demo repo '{settings.github_demo_repo}' not reachable yet ({type(exc).__name__}). Fine until Phase 2.")
    return True


if __name__ == "__main__":
    ok = check_tavily() & check_github()
    raise SystemExit(0 if ok else 1)
