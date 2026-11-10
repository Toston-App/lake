# Toston integration files

This folder holds what you need to use Toston (Clever) from an LLM client through MCP.

- `.claude-plugin/plugin.json`: Claude Code plugin manifest.
- `.mcp.json`: points the plugin at the Toston MCP server, `https://api.cleverbill.ing/mcp`.
- `skills/toston/SKILL.md`: the Toston skill, with safety rules and core workflows.
- `skills/toston/references/workflows.md`: longer workflows (budgeting, duplicate places, yearly summary).
- `skills/toston/references/tools.md`: one line per MCP tool, with whether it needs confirmation.

The skill is optional. The MCP server sends the same safety rules to every client on connect, so clients without skill support are still safe to use.

To install the plugin in Claude Code:

```text
/plugin marketplace add Toston-App/lake
/plugin install toston@toston
```

For install steps per client (Claude, ChatGPT, Grok, OpenCode, Cursor, VS Code, Codex), see [docs/MCP.md](../../docs/MCP.md).
