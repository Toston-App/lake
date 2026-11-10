# Plan 003: Ship the Toston skill, a Claude plugin, and per-client install guides

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. Do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**, from the repo root:
> `git diff --stat ffc3172..HEAD -- README.md docs/` and
> `test -f backend/app/app/mcp_server/tools.py && test -f backend/app/app/mcp_server/instructions.py && echo ok` → `ok`.
> Plans 001 and 002 must be merged into your base. If they aren't, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW (docs and packaging, plus one consistency test)
- **Depends on**: plans/001-mcp-tool-core.md, plans/002-remote-mcp-endpoint-clerk-oauth.md
- **Category**: direction / docs
- **Planned at**: commit `ffc3172`, 2026-11-09

## Why this matters

The MCP server (plans 001 and 002) gives any LLM client Toston's *tools*. A
**skill** gives it Toston's *know-how*: how to log a receipt in one go, run a
monthly review, clean up categories, and handle confirmations safely. Users
also need a copy-paste install path for each client they already pay for.

Not every client supports skills. Claude (claude.ai, Desktop, Code), OpenCode
and Codex do. ChatGPT and Grok connectors don't. So the skill must only *add
to* what the server already says in `SERVER_INSTRUCTIONS`; it must never be
required for safe use.

## Current state

- `backend/app/app/mcp_server/tools.py` (plan 001): `MCP_TOOLS: dict[str, McpTool]`, 26 tools:
  `get_user_profile, list_accounts, list_categories, list_places, search_transactions, get_period_summary, get_period_charts, list_balance_adjustments, manage_account, manage_category, manage_subcategory, manage_place, create_expense, update_expense, delete_expense, create_income, update_income, delete_income, create_transfer, update_transfer, delete_transfer, bulk_create_transactions, bulk_delete_transactions, create_balance_adjustment, confirm_action, cancel_action`.
  Risky and destructive tools return `{"status": "confirmation_required", "action_id", "preview", "message", "next_step"}`.
  Bulk calls are capped at `MCP_BULK_MAX_ITEMS` (100).
- `backend/app/app/mcp_server/instructions.py` (plan 002): `SERVER_INSTRUCTIONS`, six numbered rules.
  The skill repeats these rules and then adds workflows.
- The public endpoint is `MCP_PUBLIC_URL`. Production is planned as `https://api.cleverbill.ing/mcp`.
  Use that URL in the docs, but confirm with the operator before publishing.
- `README.md`: the "Getting Started" link list (around lines 50–60) references `docs/API_CAPABILITIES.md`
  and `docs/CHAT_API.md`. **Neither file exists in `docs/`.** Don't fix that here; it is reported separately.
- There is no `integrations/` directory and no `.claude-plugin/` anywhere in the repo yet.
  The root `.claude/` directory is the maintainer's **local, untracked** Claude Code config. Never write there.

### Agent Skills format (as of 2026-11; re-check at https://agentskills.io or Anthropic's skills docs)

A skill is a directory containing `SKILL.md` with YAML frontmatter, plus optional `references/`:
```markdown
---
name: toston
description: <one paragraph: what it does AND when to use it; this text decides when it triggers>
---
<body: instructions for the model>
```
Keep `SKILL.md` under about 300 lines and move long material into `references/*.md`, linked from the body.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Consistency test | `cd backend && uv run pytest app/tests/mcp_server/test_skill_consistency.py -q` | all pass |
| JSON validity | `python -m json.tool integrations/toston/.claude-plugin/plugin.json > /dev/null && python -m json.tool integrations/toston/.mcp.json > /dev/null && python -m json.tool .claude-plugin/marketplace.json > /dev/null` | exit 0 |
| Plugin validation (if available) | `claude plugin validate integrations/toston` | no errors (if the subcommand doesn't exist, record that and rely on JSON validity) |

## Suggested executor toolkit

- If available, use the `anthropic-skills:skill-creator` skill when drafting `SKILL.md`
  (description wording, length, and its eval loop).
- Use a web-fetch tool on each client's **official** MCP docs before writing that client's
  section. Vendor menus move often. Never write steps from memory.

## Scope

**In scope**:
- `integrations/toston/README.md` (create)
- `integrations/toston/skills/toston/SKILL.md` (create)
- `integrations/toston/skills/toston/references/workflows.md` (create)
- `integrations/toston/skills/toston/references/tools.md` (create)
- `integrations/toston/.claude-plugin/plugin.json` (create)
- `integrations/toston/.mcp.json` (create)
- `.claude-plugin/marketplace.json` at the repo root (create)
- `docs/MCP.md` (create): the user-facing install guide
- `README.md`: add one bullet linking `docs/MCP.md`
- `backend/app/tests/mcp_server/test_skill_consistency.py` (create)

**Out of scope**:
- Any server code. If the skill seems to need a tool that doesn't exist, write it down under "Maintenance notes" instead.
- `.claude/` at the repo root (local, untracked config).
- Publishing to any marketplace or registry, or making any public post. That is the operator's call.
- The missing `docs/API_CAPABILITIES.md` and `docs/CHAT_API.md`.

## Git workflow

- Branch: `advisor/003-skill-and-distribution`. Never commit to `develop`.
- Conventional commits, e.g. `docs(mcp): add install guides and Toston skill`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Write `SKILL.md`

Frontmatter:
- `name: toston`
- `description`: one paragraph. It must say that the skill manages the user's personal finances in
  Toston (also known as Clever), and name the triggers: logging an expense, income or transfer,
  receipts, "how much did I spend", budgets, monthly reviews, categorizing, cleaning up transactions,
  and account balances. It must also state that the Toston MCP connector is required.

Body sections, in order:
1. **Setup check**: if no `toston` tools are available, tell the user to connect Toston and point them to the **public** guide `https://github.com/Toston-App/lake/blob/develop/docs/MCP.md` (the skill is installed outside this repo, so a relative path won't resolve). Stop there.
2. **Safety rules**: copy rules 1–6 from `SERVER_INSTRUCTIONS` word for word, so both channels say the same thing (the test in Step 5 checks this).
3. **Core workflows**, each 5–10 lines naming the tools in order:
   - *Quick log* ("spent 12 on lunch at Joe's"): `list_accounts` and `list_categories` (call each at most once per conversation and reuse the result), match the place with `list_places` or create it with `manage_place` `create`, then `create_expense`. Ask only when the account is ambiguous.
   - *Receipt or statement photo*: extract the lines with the host's own vision, show a table to the user, then send `bulk_create_transactions` in chunks of ≤100. It returns `confirmation_required`; show the preview, and `confirm_action` only after the user agrees.
   - *Monthly review*: `get_period_summary` (`date_filter_type="month"`), then `get_period_charts`; compare with the previous month; list the top 3 categories and anything unusual; offer to recategorize.
   - *Find and fix*: `search_transactions` with filters, then `update_expense` or `update_income`. Updates are confirmation-gated, so batch the previews.
   - *Balance doesn't match the bank*: `list_accounts`, then `create_balance_adjustment` (gated). Explain that this creates an adjustment, not a transaction.
4. **Presentation**: amounts with currency and two decimals, use tables for more than 3 rows, and never print internal ids unless asked.
5. **Links**: `references/workflows.md` (longer workflows: budgeting by category, cleaning up duplicate places, yearly summary) and `references/tools.md` (one line per tool: what it does and whether it needs confirmation).

**Verify**: `wc -l integrations/toston/skills/toston/SKILL.md` → under 300, and `head -5` shows valid frontmatter with `name: toston`.

### Step 2: Write the references

- `references/tools.md`: a table with columns Tool | Purpose | Needs confirmation (yes / no / "delete & update only" for `manage_*`), covering all 26 tools. Derive the confirmation column from `chat_tools.requires_confirmation` (`backend/app/app/ai/chat_tools.py:73`): writes in `DESTRUCTIVE_TOOLS` or `RISKY_CONFIRMATION_TOOLS`, plus `manage_*` with `update`/`delete`.
- `references/workflows.md`: the three longer workflows named in Step 1.5, in the same style.

**Verify**: `grep -c "^| " integrations/toston/skills/toston/references/tools.md` → ≥27 (header plus 26 rows).

### Step 3: The Claude plugin and marketplace

- `integrations/toston/.claude-plugin/plugin.json`:
  `{"name": "toston", "description": "Use Toston (Clever) personal finance from Claude", "version": "0.1.0", "author": {"name": "Toston"}, "homepage": "https://cleverbill.ing"}`
- `integrations/toston/.mcp.json`:
  `{"mcpServers": {"toston": {"type": "http", "url": "https://api.cleverbill.ing/mcp"}}}`
- `.claude-plugin/marketplace.json` at the repo root, listing one plugin with `"source": "./integrations/toston"`.
  Fetch the current Claude Code plugin marketplace docs first and match the required fields
  (`name`, `owner`, `plugins[]`) exactly.

**Verify**: the "JSON validity" command → exit 0, and the "Plugin validation" command if it exists.

### Step 4: Install guides in `docs/MCP.md` and `integrations/toston/README.md`

`docs/MCP.md` sections:
1. What it is (2 sentences), what your LLM can and can't do, and that deletes and edits always ask you first.
2. **The URL**: `https://api.cleverbill.ing/mcp`, sign-in through the Toston login.
3. One section per client: **Claude.ai / Claude Desktop** (custom connector, then uploading the skill or installing the plugin), **Claude Code** (`claude mcp add --transport http toston https://api.cleverbill.ing/mcp`, and `/plugin marketplace add Toston-App/lake` then `/plugin install toston`), **ChatGPT** (developer mode connector; say that plan availability varies), **Grok** (custom connector at grok.com), **OpenCode** (an `opencode.json` `mcp` entry with `"type": "remote"`, plus where to put the skill), **Cursor** and **VS Code** (`mcp.json`), and **Codex** (`config.toml`). For **every** client, fetch the vendor's official doc and put its URL under the section as "Source: …". If you can't verify a client's current steps, write `> Instructions pending verification.` and leave the steps out. Never guess.
4. Do **not** add a section for Muse (Meta's assistant app). Decision D8 in `plans/README.md` defers it.
5. Troubleshooting: 401 means reconnect; `account_not_set_up` means opening the Toston app once; rate-limit messages; how to revoke access in Clerk/Toston settings ("coming soon" until the UI exists).
6. Privacy note: your LLM provider sees the finance data the tools return. Link the provider's data policy, without making claims about it.

`integrations/toston/README.md`: a 15-line pointer that names the files in the folder and links `docs/MCP.md`.
`README.md`: add `- Use Toston from Claude, ChatGPT, Grok and more (MCP): [docs/MCP.md](docs/MCP.md)` to the existing link list.

**Verify**: `grep -c "Source: http" docs/MCP.md` → at least the number of client sections that have steps.

### Step 5: Consistency test

`backend/app/tests/mcp_server/test_skill_consistency.py`:
- Locate the repo root with `Path(__file__).resolve().parents[4]` (check it contains `integrations/`), and read `SKILL.md` and `references/tools.md`.
- Test 1: every `SERVER_INSTRUCTIONS` rule line (lines starting `1.`–`6.`) appears verbatim in `SKILL.md`.
- Test 2: every backticked token in `SKILL.md` and `tools.md` that matches `^[a-z_]+$` and **starts with** one of the prefixes `list_`, `get_`, `create_`, `update_`, `delete_`, `manage_`, `bulk_`, `search_`, `confirm_`, `cancel_` is a key in `MCP_TOOLS`. This catches a skill that names a removed tool.
- Test 3: `tools.md` has a row for every key in `MCP_TOOLS`.

**Verify**: `cd backend && uv run pytest app/tests/mcp_server/test_skill_consistency.py -q` → 3 passed.

## Test plan

Step 5 covers drift between the skill, the docs and the server. Manual check
(operator): install the plugin in Claude Code, then ask "log 4.50 coffee today" and
"review last month". Record both in the PR.

## Done criteria

- [ ] Every file in Scope exists, and `git status` shows nothing else changed
- [ ] The JSON validity command exits 0
- [ ] `uv run pytest app/tests/mcp_server -q` → all pass (plans 001–003)
- [ ] `SKILL.md` is under 300 lines, with frontmatter `name: toston`
- [ ] Every client section in `docs/MCP.md` has a `Source:` URL or the "pending verification" note
- [ ] `plans/README.md` row 003 updated

## STOP conditions

- `SERVER_INSTRUCTIONS` or `MCP_TOOLS` differ from what is listed under "Current state". Report the difference, because the skill would teach wrong tool names.
- The Claude Code marketplace format requires the marketplace at a location that conflicts with this repo's layout (for example, it can't point at a subdirectory). Report it, since it affects decision D6.
- A vendor's official docs say remote MCP over OAuth is unsupported on that client. Record it in the support matrix in `plans/README.md` instead of writing a workaround.

## Maintenance notes

- `SERVER_INSTRUCTIONS` is the canonical text. Change it there first, then mirror it in `SKILL.md`. The test enforces this.
- Bump `plugin.json` `version` whenever the skill changes, because Claude Code caches plugins by version.
- Candidates for later (not planned): an MCP Registry `server.json` entry, MCP `prompts` such as `monthly_review` for clients that surface prompts, moving `integrations/toston/` to its own public repo (D6), and a localized Spanish skill (the chat UI already supports `es`).
