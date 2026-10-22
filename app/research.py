"""RESEARCH: live web search with Tavily, called by the fix agent as a tool.

When the agent meets an unfamiliar error message or is unsure how a library
or browser API behaves, it calls `web_search`. Tavily returns a short answer
plus the most relevant sources, which go straight back into the agent's
context. Every query and every source used is logged, and the log is shown
in the UI and in the pull request, so it is clear what the fix relied on.
"""

from __future__ import annotations

import time
from functools import lru_cache

from pydantic import BaseModel, Field
from tavily import TavilyClient

from app.config import settings

MAX_RESULTS = 3
SNIPPET_CHARS = 600
EXTRACT_CHARS = 4000


class Source(BaseModel):
    title: str
    url: str
    snippet: str


class ResearchEntry(BaseModel):
    """One Tavily call and what came back."""

    kind: str = Field(description="'search' or 'extract'")
    query: str = Field(description="The search query, or the URL that was extracted")
    answer: str | None = None
    sources: list[Source] = Field(default_factory=list)
    latency_s: float = 0.0


class ResearchError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client() -> TavilyClient:
    if not settings.tavily_api_key or "your" in settings.tavily_api_key:
        raise ResearchError("TAVILY_API_KEY is missing; add it to .env to enable web research.")
    return TavilyClient(api_key=settings.tavily_api_key)


def search(query: str) -> ResearchEntry:
    """Basic-depth Tavily search with a short synthesized answer (1 API credit)."""
    start = time.perf_counter()
    resp = _client().search(query=query, max_results=MAX_RESULTS, search_depth="basic", include_answer=True)
    return ResearchEntry(
        kind="search", query=query, answer=resp.get("answer"),
        sources=[Source(title=r.get("title", ""), url=r.get("url", ""), snippet=(r.get("content") or "")[:SNIPPET_CHARS])
                 for r in resp.get("results", [])],
        latency_s=round(time.perf_counter() - start, 2),
    )


def extract(url: str) -> ResearchEntry:
    """Fetch the readable text of one page found by `search` (e.g. a docs page)."""
    start = time.perf_counter()
    resp = _client().extract(urls=[url])
    pages = resp.get("results", [])
    text = (pages[0].get("raw_content") or "")[:EXTRACT_CHARS] if pages else ""
    return ResearchEntry(kind="extract", query=url, sources=[Source(title=url, url=url, snippet=text)],
                         latency_s=round(time.perf_counter() - start, 2))


def as_tool_result(entry: ResearchEntry) -> str:
    """Compact text for the agent's context."""
    lines = [f"Answer: {entry.answer}"] if entry.answer else []
    for i, s in enumerate(entry.sources, 1):
        lines.append(f"[{i}] {s.title} ({s.url})\n{s.snippet}")
    return "\n\n".join(lines) or "No results."
