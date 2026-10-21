"""Tavily web search for the finance chat agent."""

from __future__ import annotations

import httpx

from app.core.config import settings


async def search_web(
    query: str,
    *,
    topic: str = "general",
    max_results: int | None = None,
) -> dict:
    """Search the web via Tavily and return normalized results."""
    if not settings.TAVILY_API_KEY:
        raise ValueError("TAVILY_API_KEY is not configured")

    limit = max_results or settings.CHAT_WEB_SEARCH_MAX_RESULTS
    payload: dict = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "max_results": limit,
        "include_answer": True,
        "search_depth": "basic",
    }
    if topic in ("news", "finance"):
        payload["topic"] = topic

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post("https://api.tavily.com/search", json=payload)
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("results", []):
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content") or item.get("snippet"),
                "published_date": item.get("published_date"),
            }
        )

    return {
        "query": query,
        "answer": data.get("answer"),
        "results": results,
    }
