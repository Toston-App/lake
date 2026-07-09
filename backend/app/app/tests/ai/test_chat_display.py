from app.ai.chat_tools import build_system_prompt


def test_build_system_prompt_mentions_ui_rendering():
    prompt = build_system_prompt({"accounts": [], "categories": [], "places": []})
    assert "client renders structured UI" in prompt
    assert "Bootstrap data:" in prompt


def test_build_system_prompt_includes_today():
    prompt = build_system_prompt({"accounts": [], "categories": [], "places": []})
    assert "Today is" in prompt
