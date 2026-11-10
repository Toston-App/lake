"""Keep the shipped Toston skill in sync with the MCP server."""

import re
from pathlib import Path

import pytest
from app.mcp_server.instructions import SERVER_INSTRUCTIONS
from app.mcp_server.tools import MCP_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = REPO_ROOT / "integrations" / "toston" / "skills" / "toston"
TOOL_PREFIXES = (
    "list_",
    "get_",
    "create_",
    "update_",
    "delete_",
    "manage_",
    "bulk_",
    "search_",
    "confirm_",
    "cancel_",
)


def _read(path: Path) -> str:
    assert (REPO_ROOT / "integrations").is_dir()
    return path.read_text(encoding="utf-8")


def test_skill_repeats_server_rules_verbatim() -> None:
    skill = _read(SKILL_DIR / "SKILL.md")
    rules = [
        line
        for line in SERVER_INSTRUCTIONS.splitlines()
        if re.match(r"^[1-6]\.", line)
    ]
    assert len(rules) == 6
    for rule in rules:
        assert rule in skill


@pytest.mark.parametrize("name", ["SKILL.md", "references/tools.md", "references/workflows.md"])
def test_backticked_tool_names_exist(name: str) -> None:
    text = _read(SKILL_DIR / name)
    tokens = set(re.findall(r"`([^`\n]+)`", text))
    candidates = {
        t for t in tokens if re.fullmatch(r"[a-z_]+", t) and t.startswith(TOOL_PREFIXES)
    }
    unknown = candidates - set(MCP_TOOLS)
    assert not unknown


def test_tools_reference_covers_every_tool() -> None:
    text = _read(SKILL_DIR / "references" / "tools.md")
    missing = [name for name in MCP_TOOLS if f"| `{name}` |" not in text]
    assert not missing
