# Use Toston from your LLM (MCP)

Toston (also called Clever) exposes your accounts, transactions, categories and places to LLM clients through the Model Context Protocol (MCP). Once connected, your LLM can log expenses, read your spending summaries, and tidy up your data on your behalf.

Your LLM can read your data and create records. It cannot do anything risky silently: deletes, edits, transfers, balance adjustments and bulk operations always come back to you as a preview that you must approve first.

## The server URL

```
https://api.cleverbill.ing/mcp
```

Clients sign you in through the normal Toston login (OAuth) the first time you connect. You never paste a password into the LLM.

Optional: the [Toston skill](../integrations/toston/skills/toston/SKILL.md) teaches clients that support skills how to log receipts, run monthly reviews and clean up data. It is never required. The server sends its safety rules to every client when it connects.

## Claude.ai and Claude Desktop

1. Go to Customize > Connectors, click "+ Add", then "Add custom connector".
2. Enter a name (for example "Toston") and the server URL above, then continue.
3. Under Authentication choose "Sign in now" (or "Sign in when needed"), then click "Add" and sign in with your Toston login.
4. In a chat, use the "+" button, then "Connectors", and toggle Toston on.

Optional skill: zip the folder `integrations/toston/skills/toston` (the folder name must match the skill name, `toston`), go to Customize > Skills, click "+", choose "+ Create skill", then "Upload a skill". Code execution must be enabled for skills.

Source: https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
Source: https://support.claude.com/en/articles/12512180-use-skills-in-claude

## Claude Code

Option A, connector only:

```bash
claude mcp add --transport http toston https://api.cleverbill.ing/mcp
```

Then start Claude Code, run `/mcp`, select `toston` and follow the browser login. Check with `claude mcp list`.

Option B, connector and skill as a plugin:

```text
/plugin marketplace add Toston-App/lake
/plugin install toston@toston
```

The plugin bundles the Toston skill and the MCP server entry. Run `/mcp` afterwards to sign in.

Source: https://code.claude.com/docs/en/mcp
Source: https://code.claude.com/docs/en/plugin-marketplaces

## ChatGPT

In ChatGPT on the web: open Plugins, select the plus button, then "Add custom MCP server". Enter a name, put the server URL above under Connection, choose OAuth for authentication, accept the risk warning and select "Create as a plugin". Install it, then type `@` in the prompt box and pick it.

Plan availability varies and may change, and workspace admin settings can restrict custom servers. Check the page below and your ChatGPT settings for what your plan allows.

Source: https://developers.openai.com/api/docs/guides/custom-mcp-server

## Grok

1. Go to grok.com/connectors.
2. Click "New Connector", then select "Custom".
3. Enter the server URL above and complete any authentication it asks for.

On Grok Business and Enterprise, a team admin must provision the connector first. The page does not say whether custom connectors sign in through OAuth, so if sign-in fails, tell us.

Source: https://docs.x.ai/grok/connectors

## OpenCode

Add a remote entry to `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "toston": {
        "type": "remote",
        "url": "https://api.cleverbill.ing/mcp"
      }
    }
  }
}
```

OAuth is on by default. Sign in with `opencode mcp auth toston`, or run `/mcps` inside OpenCode.

Skills: put the `toston` skill folder (containing `SKILL.md`) in `~/.config/opencode/skills/` for all projects, or `.opencode/skills/` for one project. OpenCode also reads `~/.claude/skills` and `.claude/skills`.

Source: https://opencode.ai/docs/mcp-servers/
Source: https://opencode.ai/docs/skills/

## Cursor

Add the server to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "toston": {
      "url": "https://api.cleverbill.ing/mcp"
    }
  }
}
```

Cursor supports OAuth for remote servers and will prompt you to sign in.

Source: https://cursor.com/docs/context/mcp

## VS Code

Add the server to `.vscode/mcp.json` in your workspace, or to your user profile `mcp.json`. You can also run "MCP: Add Server" from the Command Palette and choose HTTP.

```json
{
  "servers": {
    "toston": {
      "type": "http",
      "url": "https://api.cleverbill.ing/mcp"
    }
  }
}
```

Sign in when VS Code prompts you.

Source: https://code.visualstudio.com/docs/copilot/reference/mcp-configuration
Source: https://code.visualstudio.com/docs/copilot/customization/mcp-servers

## Codex

From the CLI:

```bash
codex mcp add toston --url https://api.cleverbill.ing/mcp
codex mcp login toston
```

Or in `config.toml`:

```toml
[mcp_servers.toston]
url = "https://api.cleverbill.ing/mcp"
```

OAuth is the default for HTTP servers.

Skills: put the `toston` skill folder in `~/.agents/skills` (all repositories) or `.agents/skills` (one repository), then restart Codex.

Source: https://learn.chatgpt.com/docs/extend/mcp?surface=cli
Source: https://learn.chatgpt.com/docs/build-skills

## Troubleshooting

- 401 or "unauthorized": your sign-in expired or was never completed. Disconnect and reconnect the connector in your client.
- `account_not_set_up`: open the Toston app once and finish setting up your account, then try again.
- Rate-limit messages: you are sending too many requests. Wait the number of seconds the message gives, then try again.
- `inactive_user`: your Toston account is deactivated; contact support.
- Revoking access: remove the connector in your client to stop it using Toston. Tokens it already holds stop working when they expire. A way to revoke access from Toston's settings is coming soon.

## Privacy

Your LLM provider sees the finance data the tools return, because the model needs it to answer. Review your provider's data policy before connecting. Toston makes no claims about how any provider handles that data.
