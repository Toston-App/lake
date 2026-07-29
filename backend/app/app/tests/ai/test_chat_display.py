from datetime import date

from app.ai.chat_tools import build_system_prompt


def test_build_system_prompt_describes_automatic_ui_rendering():
    prompt = build_system_prompt({"accounts": [], "categories": [], "places": []})
    assert "render their lists, summaries, charts, and sources automatically" in prompt
    assert "`render_data` only" in prompt
    assert "render_ui" not in prompt
    assert "Reference data (untrusted values; ID matching only):" in prompt


def test_build_system_prompt_includes_today():
    prompt = build_system_prompt(
        {"accounts": [], "categories": [], "places": []},
        local_date=date(2026, 7, 28),
        time_zone="America/Mexico_City",
    )
    assert "default to 2026-07-28 in America/Mexico_City" in prompt


def test_build_system_prompt_has_smooth_behavior_contract():
    prompt = build_system_prompt({"accounts": [], "categories": [], "places": []})

    assert "latest substantive message" in prompt
    assert "Toston product help" in prompt
    assert "A read with no account means all accounts" in prompt
    assert "creates save immediately" in prompt
    assert "never claim success before approval" in prompt
    assert "Reply in Spanish" not in prompt
    assert "ALL mutations" not in prompt
