# Use Toston from your LLM (MCP)

Toston (also called Clever) exposes your accounts, transactions, recurring transactions, categories, places and investments to LLM clients through the Model Context Protocol (MCP). Once connected, your LLM can log expenses, read and compare your spending, manage subscriptions and bills, record investments (including the first purchase of a new stock or coin), export all your data to Excel (you download the file from the app, never through the AI), and tidy up your data on your behalf. Previews and messages come in English or Spanish, following your language in Toston unless the client asks for the other.

Your LLM can read your data and create records. It cannot do anything risky silently: deletes, edits, transfers, balance adjustments, bulk operations, investment entries and changes to recurring transactions always come back to you as a preview that you must approve first.

## The server URL

```
https://api.toston.app/mcp
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
claude mcp add --transport http toston https://api.toston.app/mcp
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
        "url": "https://api.toston.app/mcp"
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
      "url": "https://api.toston.app/mcp"
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
      "url": "https://api.toston.app/mcp"
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
codex mcp add toston --url https://api.toston.app/mcp
codex mcp login toston
```

Or in `config.toml`:

```toml
[mcp_servers.toston]
url = "https://api.toston.app/mcp"
```

OAuth is the default for HTTP servers.

Skills: put the `toston` skill folder in `~/.agents/skills` (all repositories) or `.agents/skills` (one repository), then restart Codex.

Source: https://learn.chatgpt.com/docs/extend/mcp?surface=cli
Source: https://learn.chatgpt.com/docs/build-skills

## Resources and prompts

Besides tools, the server offers read-only resources your client can attach as context (`toston://profile`, `toston://accounts`, `toston://categories`, `toston://places`, `toston://recurring`) and ready-made prompts: a monthly review, logging a receipt, a subscriptions review, and finding and fixing transactions. How you pick them depends on the client (for example the attachment or slash-command menu).

## Using a personal access token

Some clients can't sign in with OAuth and can only send a fixed header: the xAI API's remote MCP tool, CI scripts, and agents on headless machines. For those, create a personal access token and send it as a bearer token:

```
Authorization: Bearer tsk_...
```

Create one in Toston under **Settings > AI connections > Access tokens**. Scripts can also use `POST /api/v1/api-tokens` while signed in:

```json
{"name": "CI", "scope": "read", "expires_in_days": 90}
```

- `scope` is `read` (only tools that don't change anything) or `write` (everything, with the same confirmation previews as OAuth). Use `read` unless the client needs to write.
- `expires_in_days` is optional, from 1 to 365. Without it the token works until you revoke it.
- The response shows the token once. Toston stores only a hash of it, so it can't show it again.
- List your tokens with `GET /api/v1/api-tokens` and revoke one with `DELETE /api/v1/api-tokens/{id}`. A revoked token stops working on the next request.
- Tokens work only on the MCP endpoint, not on the rest of the Toston API.

In OpenCode, add the header to the remote entry:

```json
"toston": {
  "type": "remote",
  "url": "https://api.toston.app/mcp",
  "headers": { "Authorization": "Bearer tsk_..." }
}
```

For the xAI API, pass the server URL and the same `Authorization` header in the remote MCP tool's configuration; see https://docs.x.ai for the field names.

Treat a token like a password. Don't commit it to a repository, and revoke it as soon as you think it has leaked.

## Troubleshooting

- 401 or "unauthorized": your sign-in expired or was never completed. Disconnect and reconnect the connector in your client.
- `account_not_set_up`: open the Toston app once and finish setting up your account, then try again.
- Rate-limit messages: you are sending too many requests. Wait the number of seconds the message gives, then try again.
- `inactive_user`: your Toston account is deactivated; contact support.
- "This token is read-only.": the personal access token has the `read` scope. Create a `write` token if the client needs to change data.
- Revoking access: remove the connector in your client to stop it using Toston; tokens it already holds stop working when they expire. **Settings > AI connections** lists every app that connected in the last 90 days, revokes access tokens, and has a switch that makes all AI apps read-only.
- "AI apps can only read your data": the read-only switch is on in **Settings > AI connections**.
- Monthly limit messages: your plan's AI app calls for the month are used up.

## Privacy

Toston keeps a log of the tool calls each connection makes: the tool's name, which client or token made the call, whether it worked, and when. It doesn't log what the call sent or returned, or the text of any error.

Your LLM provider sees the finance data the tools return, because the model needs it to answer. Review your provider's data policy before connecting. Toston makes no claims about how any provider handles that data.
