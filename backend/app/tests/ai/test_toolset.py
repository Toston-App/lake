from app.ai.agent import build_toolset
from app.core.config import settings


def test_every_tier_gets_the_full_chat_toolset(monkeypatch):
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "key")

    tools = set(build_toolset().tools)

    assert {
        "get_period_summary",
        "get_period_charts",
        "bulk_create_transactions",
        "bulk_delete_transactions",
        "search_web",
        "delete_expense",
        "render_data",
    } <= tools


def test_web_search_still_needs_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "TAVILY_API_KEY", None)

    assert "search_web" not in build_toolset().tools
