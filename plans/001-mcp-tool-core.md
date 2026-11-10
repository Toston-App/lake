# Plan 001: Add a transport-agnostic MCP tool core with server-side confirmation

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. Do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**, from the repo root:
> `git diff --stat ffc3172..HEAD -- backend/app/app/ai/chat_tools.py backend/app/app/ai/tools.py backend/app/app/schemas/expense.py backend/app/app/schemas/income.py backend/app/app/core/config.py backend/app/app/utilities/redis.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding. On a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (it lets an external LLM write to users' finances; mitigated by the confirmation step)
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `ffc3172`, 2026-11-09

## Why this matters

Toston wants users to drive their finances from their own LLM (Claude,
ChatGPT, Grok, OpenCode…) over the Model Context Protocol (MCP). The backend
already has a typed, ownership-checked tool dispatcher for its in-app chat,
`execute_tool`. This plan builds the layer between MCP and that dispatcher,
with no MCP library yet:

1. a registry of which tools are exposed, with JSON Schemas for their inputs;
2. a `call_tool` function that validates input, enforces the confirmation
   policy and rate limits, and returns plain dicts.

Plan 002 then wires it to the HTTP transport and OAuth. Keeping the core
transport-free makes it unit-testable with the repo's existing mock-based test
style, and lets plan 004 (personal access tokens) reuse it unchanged.

Risky and destructive operations must **not** run on the first call. External
hosts may auto-approve tool calls, and the user's LLM context can contain
prompt-injected text. Such calls return a preview plus `action_id`. Only an
explicit `confirm_action(action_id)` executes them, the same policy the in-app
chat already enforces.

## Current state

Paths below are relative to `backend/app/app/` (the Python package `app`).

- `ai/chat_tools.py`: the in-app chat's tool dispatcher and confirmation policy.
  - Policy sets and helpers (`ai/chat_tools.py:34-78`):
    ```python
    DESTRUCTIVE_TOOLS = frozenset({"delete_expense","delete_income","delete_transfer","bulk_delete_transactions"})
    MANAGE_DELETE_ACTIONS = frozenset({"manage_account","manage_category","manage_subcategory","manage_place"})
    RISKY_CONFIRMATION_TOOLS = frozenset({"create_transfer","update_expense","update_income","update_transfer","create_balance_adjustment","bulk_create_transactions"})
    def is_destructive_tool(tool_name: str, args: dict) -> bool: ...
    def requires_confirmation(tool_name: str, args: dict) -> bool:
        if is_destructive_tool(tool_name, args):
            return True
        if tool_name in MANAGE_DELETE_ACTIONS and args.get("action") == "update":
            return True
        return tool_name in RISKY_CONFIRMATION_TOOLS
    ```
  - `merge_confirm_tool_args(tool_name, stored_args, override)` (`:81`) merges
    user edits into stored args at confirm time.
  - `build_pending_preview(db, user, tool_name, tool_args) -> dict` (`:282`) and
    `format_confirmation_message(tool_name, preview, response_language="en") -> str` (`:495`).
  - `execute_tool(tool_name, args, db, user, *, confirmed=False) -> tuple[dict, str | None]`
    (`:756`). It returns `({"success": True, "data": ...}, action_type)` or
    `({"success": False, "error": msg}, None)` and never raises. It hard-codes the
    record source, for example at `:794-799`:
    ```python
    if tool_name == "create_expense":
        data = arg_model.model_dump(exclude_none=True)
        data.setdefault("made_from", CHAT_MADE_FROM)
        obj = schemas.ExpenseCreate(**data)
    ```
    The same `setdefault("made_from", CHAT_MADE_FROM)` appears for `create_income`
    (`:814`), and `made_from=CHAT_MADE_FROM` is passed to `bulk_create_transactions` (`:920`).
    Tools without arg models (`get_user_profile`, `list_accounts`,
    `list_categories`, `list_places`) go to `_execute_no_arg_tool` (`:1079`).
- `ai/tools.py`: pydantic arg models. `TOOL_ARG_MODELS: dict[str, type[BaseModel]]` at `:482`.
  Write models include a **required** `response_language: Literal["en","es"]`
  (`response_language_field()` at `:35`, `Field(...)`). Read models inherit
  `presentation: Literal["model_only","user_visible"] = "model_only"` from
  `ReadPresentationArgs` (`:59`). Both fields only matter to Toston's own chat UI.
- `schemas/expense.py:34-40` and `schemas/income.py:33-39`: the `made_from` whitelist.
  ```python
  @validator("made_from")
  def validate_made_from(cls, v):
      if v not in ["Web", "WhatsApp", "OCR", "Chat", "Recurring"]:
          raise ValueError("made_from must be one of: Web, WhatsApp, OCR, Chat, Recurring")
      return v
  ```
  The DB column is a plain `String` (`models/expense.py:41`, `models/income.py:38`),
  so **no migration** is needed to add a value.
- `utilities/redis.py`: Upstash async client `r` (`:13`), plus
  `store_pending_action(action_id, user_id, conversation_id, tool_name, tool_args, preview, response_language="en", expire_time=CHAT_PENDING_TTL, turn_id=None)` (`:465`),
  `get_pending_action(action_id, user_id) -> dict | None` (`:500`, returns None if
  it belongs to another user), `delete_pending_action(action_id)` (`:514`), and
  `_chat_pending_key(action_id)` (`:236`). `CHAT_PENDING_TTL = 1800` (`:159`).
- `services/rate_limits.py`: `enforce_rate_limit(key, limit, *, window_seconds=3600, fail_open: bool)`.
  It raises `RateLimitedError` (a `ServiceError`, status 429) when over the limit.
  The pattern to copy is `_enforce_chat_rate_limit` in `api/api_v1/endpoints/chat.py:168-177`:
  ```python
  windows = ((60, settings.CHAT_RATE_LIMIT_PER_MINUTE), (86_400, settings.CHAT_RATE_LIMIT_PER_DAY))
  for window_seconds, limit in windows:
      await enforce_rate_limit(f"chat:{user_id}", limit, window_seconds=window_seconds, fail_open=False)
  ```
- `core/config.py:149-171`: the `# Chat agent` settings block. Add new settings in a
  sibling block with the same comment style.

**Conventions**: modules start with a docstring explaining why they exist
(see `ai/agent.py:1-24`). Code is type-hinted, uses `from __future__ import annotations`,
and has small private helpers prefixed `_`. Tests live in `backend/app/tests/`
(**not** `backend/app/app/tests/`, which is legacy). They mock services with
`SimpleNamespace` and `AsyncMock` rather than hitting the DB. The exemplar is
`backend/app/tests/ai/test_search_transactions.py`.

## Commands you will need

Run from `backend/`.

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Unit tests (this plan) | `uv run pytest app/tests/mcp_server -q` | all pass |
| Existing AI tests | `uv run pytest app/tests/ai -q` | all pass (no regressions) |
| Lint | `uv run ruff check app/app/mcp_server app/tests/mcp_server app/app/ai/chat_tools.py` | `All checks passed!` |
| Format check | `uv run ruff format --check app/app/mcp_server app/tests/mcp_server` | exit 0 |

The full suite needs the test DB (`docker compose -f docker-compose.test.yml up -d`
from the repo root, then `uv run pytest`). The new tests in this plan must **not** need it.

## Scope

**In scope** (the only files you should modify):
- `backend/app/app/mcp_server/__init__.py` (create, empty docstring module)
- `backend/app/app/mcp_server/tools.py` (create)
- `backend/app/app/ai/chat_tools.py`: only add a `made_from` keyword to `execute_tool` (Step 2)
- `backend/app/app/schemas/expense.py`, `backend/app/app/schemas/income.py`: add `"MCP"` to the whitelist
- `backend/app/app/core/config.py`: add the `# MCP server` settings block
- `backend/app/app/utilities/redis.py`: add `claim_pending_action`
- `backend/app/tests/mcp_server/test_tools.py` (create)

**Out of scope** (do NOT touch):
- `ai/agent.py` and `api/api_v1/endpoints/chat.py`: the in-app chat must behave exactly as before.
- Any MCP SDK dependency or HTTP endpoint (that is plan 002).
- Investments and recurring tools (paid features, deferred).
- `ask_user`, `render_data`, `search_web`, `submit_feedback`: deliberately not exposed (see "Why").
- The package is named `mcp_server`, **not** `mcp`, so it can never shadow the `mcp` SDK that plan 002 installs.

## Git workflow

- Branch: `advisor/001-mcp-tool-core`, created from `develop`. Never commit to `develop`.
- Conventional commits, matching `git log`, e.g. `feat(mcp): add transport-agnostic tool registry`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Allow `made_from="MCP"`

In `schemas/expense.py` and `schemas/income.py`, change the list to
`["Web", "WhatsApp", "OCR", "Chat", "Recurring", "MCP"]` and update the error
string to match. Also add `"MCP"` to the trailing comment on the column in
`models/expense.py:41` and `models/income.py:38`. That is a comment-only change:
**do not** generate a migration, and add these two model files to your commit.

**Verify**: `cd backend && uv run python -c "from app.schemas import ExpenseCreate; print(ExpenseCreate(amount=1, made_from='MCP').made_from)"` → `MCP`.
If the import path fails, use `from app.schemas.expense import ExpenseCreate`.

### Step 2: Let `execute_tool` take the record source

In `ai/chat_tools.py`, change the signature to:
```python
async def execute_tool(
    tool_name: str,
    args: dict,
    db: AsyncSession,
    user: models.User,
    *,
    confirmed: bool = False,
    made_from: str = CHAT_MADE_FROM,
) -> tuple[dict, str | None]:
```
Replace `CHAT_MADE_FROM` with `made_from` **only** in the three spots inside
`execute_tool`: `create_expense` (`setdefault`), `create_income` (`setdefault`) and
`bulk_create_transactions` (`made_from=` kwarg). Leave `build_pending_preview`
alone; the preview label is cosmetic.

**Verify**: `uv run pytest app/tests/ai -q` → all pass, so the chat's default behavior is unchanged.

### Step 3: Add settings

In `core/config.py`, directly after the `# Chat agent` block, add:
```python
    # MCP server (remote tools for external LLM clients, see plans/README.md)
    MCP_ENABLED: bool = False
    # Per-user tool-call limits for /mcp. 0 disables a window.
    MCP_RATE_LIMIT_PER_MINUTE: int = 60
    MCP_RATE_LIMIT_PER_DAY: int = 2_000
    # When True, only users whose tier is paid may use MCP (decision D3).
    MCP_REQUIRES_PAID: bool = False
    # Max incomes+expenses in one bulk_create/bulk_delete call over MCP.
    # schemas/bulk.py has no cap of its own.
    MCP_BULK_MAX_ITEMS: int = 100
```
**Verify**: `uv run python -c "from app.core.config import settings; print(settings.MCP_ENABLED, settings.MCP_RATE_LIMIT_PER_MINUTE)"` → `False 60`.
This needs the `.env` that the other settings require. If it fails on unrelated
missing env vars, run it inside the test environment with the env vars that
`app/tests/conftest.py:29-60` sets.

### Step 4: Add an atomic claim for pending actions

In `utilities/redis.py`, after `delete_pending_action`, add:
```python
async def claim_pending_action(action_id: str, user_id: int) -> dict | None:
    """Load and delete a pending action in one winner-takes-all step.

    Two concurrent confirms of the same action must not both execute it. Only
    the caller whose DELETE removes the key gets the payload; the other gets None.
    """
    pending = await get_pending_action(action_id, user_id)
    if pending is None:
        return None
    deleted = await r.delete(_chat_pending_key(action_id))
    return pending if deleted == 1 else None
```
(The in-app chat doesn't need this because it serializes per conversation.
MCP calls have no such lock.)

**Verify**: `uv run ruff check app/app/utilities/redis.py` → `All checks passed!`

### Step 5: Create `mcp_server/tools.py`, the registry and schemas

Create the module with a docstring that explains its role (an MCP-facing
adapter over `ai.chat_tools.execute_tool`; no transport). Contents:

1. Constants:
   ```python
   MCP_MADE_FROM = "MCP"
   MCP_CONVERSATION_ID = "mcp"   # pending actions are stored with this conversation id
   CONFIRM_TOOL = "confirm_action"
   CANCEL_TOOL = "cancel_action"
   _CHAT_ONLY_FIELDS = ("presentation", "response_language")
   ```
2. A frozen dataclass `McpTool(name: str, description: str, arg_model: type[BaseModel] | None, read_only: bool, destructive: bool)`.
3. `MCP_TOOLS: dict[str, McpTool]` with **exactly** these entries. Write the
   descriptions for an external LLM that has never seen Toston: one or two
   sentences, saying when to use the tool and that ids come from the `list_*` tools.
   - read-only (`read_only=True`, `destructive=False`): `get_user_profile` (arg_model None), `list_accounts` (None), `list_categories` (None), `list_places` (None), `search_transactions` (`SearchTransactionsArgs`), `get_period_summary` (`GetPeriodArgs`), `get_period_charts` (`GetPeriodArgs`), `list_balance_adjustments` (`ListBalanceAdjustmentsArgs`)
   - writes (`read_only=False`): `manage_account`, `manage_category`, `manage_subcategory`, `manage_place`, `create_expense`, `update_expense`, `delete_expense`, `create_income`, `update_income`, `delete_income`, `create_transfer`, `update_transfer`, `delete_transfer`, `bulk_create_transactions`, `bulk_delete_transactions`, `create_balance_adjustment`. Take each `arg_model` from `TOOL_ARG_MODELS[name]`. Set `destructive=True` for the `delete_*` tools, `bulk_delete_transactions` and the four `manage_*` tools (since they can delete).
   - `confirm_action` and `cancel_action`, with `arg_model` set to two new models defined in this file:
     ```python
     class ConfirmActionArgs(BaseModel):
         action_id: str = Field(..., description="The action_id returned by a tool whose status was confirmation_required.")
         edits: dict[str, Any] | None = Field(default=None, description="Optional field changes the user asked for before confirming, e.g. {\"amount\": 12.5}.")
     class CancelActionArgs(BaseModel):
         action_id: str = Field(..., description="The action_id to discard.")
     ```
     `confirm_action`: `read_only=False, destructive=True`. `cancel_action`: `read_only=False, destructive=False`.
   - Assert at import time that every non-confirm name is in `TOOL_ARG_MODELS` or in the four no-arg tools, so renames in `ai/tools.py` fail loudly:
     `assert set(MCP_TOOLS) - {CONFIRM_TOOL, CANCEL_TOOL} <= set(TOOL_ARG_MODELS) | {"get_user_profile","list_accounts","list_categories","list_places"}`
4. `def input_schema(tool: McpTool) -> dict[str, Any]`:
   - `None` arg model → `{"type": "object", "properties": {}, "additionalProperties": False}`.
   - Otherwise take `tool.arg_model.model_json_schema()`, then **inline every `$ref`**
     (`"#/$defs/Name"`) recursively and drop `$defs`. Some MCP clients mishandle `$ref`.
     None of these models is self-recursive. If inlining hits a cycle, raise `RuntimeError`.
   - Remove `_CHAT_ONLY_FIELDS` from `properties` and from `required` (drop `required` if it becomes empty).
   - Drop the top-level `title`.
5. `def list_tools() -> list[dict[str, Any]]` returns, for each tool,
   `{"name", "description", "input_schema", "read_only", "destructive"}`.
   Plan 002 maps these to MCP `Tool` objects and annotations.

**Verify**: `uv run python -c "from app.mcp_server.tools import list_tools; import json; t=list_tools(); print(len(t)); print('\$ref' in json.dumps(t), 'response_language' in json.dumps(t))"` → `26` then `False False`.

### Step 6: Add `call_tool` to `mcp_server/tools.py`

```python
async def call_tool(name: str, arguments: dict[str, Any] | None, db: AsyncSession, user: models.User) -> dict[str, Any]:
```
Behavior, in order:

1. Unknown `name` → return `{"success": False, "error": f"Unknown tool: {name}"}`.
2. Copy `arguments or {}` and drop any `_CHAT_ONLY_FIELDS` keys the client sent.
   If `MCP_TOOLS[name].arg_model` is in `TOOL_ARG_MODELS.values()` and declares
   `response_language`, set `args["response_language"] = "en"`. The models require
   it, and the confirmation message is English for MCP.
3. `confirm_action`: validate with `ConfirmActionArgs` (return `{"success": False, "error": "Invalid arguments ..."}` on `ValidationError`).
   First `peek = await get_pending_action(action_id, user.id)`. If it is `None` or
   `peek["conversation_id"] != MCP_CONVERSATION_ID`, return
   `{"success": False, "error": "Action not found, expired, or already confirmed"}` **without claiming**.
   That way a wrong-conversation confirm never deletes an in-app chat approval card.
   Then call `pending = await claim_pending_action(action_id, user.id)`, which returns the same
   error on `None` (a concurrent confirm won the race).
   **Refuse non-empty `edits`** with `"This action can't be edited; cancel it and ask again."` when
   the stored tool is in `DESTRUCTIVE_TOOLS`, is `bulk_create_transactions`, or is a `manage_*` tool
   whose stored action is `"delete"`. For those tools `merge_confirm_tool_args` does
   `{**stored, **edits}`, so an edit could swap the previewed ids or items (deleting un-previewed
   records, or bypassing `MCP_BULK_MAX_ITEMS`).
   Before merging, **remove the key `id` from `edits`**. `merge_confirm_tool_args` lets an override
   change `id` for `update_*` tools, which would retarget a different record than the one previewed.
   Otherwise `tool_args = merge_confirm_tool_args(pending["tool_name"], pending["tool_args"], edits)`,
   `tool_args.setdefault("response_language", "en")`, and return the first element of
   `await execute_tool(pending["tool_name"], tool_args, db, user, confirmed=True, made_from=MCP_MADE_FROM)`.
   Re-check the confirmation policy against **edited** args: if
   `pending["tool_name"]` is `manage_*` and `tool_args.get("action")` differs from the
   stored action, refuse with an error, so an edit can't turn an update into a delete.
4. `cancel_action`: validate, then `await delete_pending_action(action_id)` **only if**
   `await get_pending_action(action_id, user.id)` returns a dict whose
   `conversation_id == MCP_CONVERSATION_ID`. Return `{"success": True, "data": {"cancelled": True}}`,
   or the not-found error.
5. For `bulk_create_transactions` and `bulk_delete_transactions`: if
   `len(args.get("incomes") or []) + len(args.get("expenses") or []) > settings.MCP_BULK_MAX_ITEMS`,
   return `{"success": False, "error": f"At most {settings.MCP_BULK_MAX_ITEMS} items per bulk call; split the request."}`
   and store nothing. Zero items also returns an error (`"Nothing to create/delete"`).
6. Any other tool: if `requires_confirmation(name, args)`:
   - Validate first: `TOOL_ARG_MODELS[name](**args)`, returning the invalid-arguments error on failure.
     **Never stash unvalidated args.**
   - `preview = await build_pending_preview(db, user, name, args)`
   - `action_id = str(uuid.uuid4())`
   - `await store_pending_action(action_id=action_id, user_id=user.id, conversation_id=MCP_CONVERSATION_ID, tool_name=name, tool_args=args, preview=preview, response_language="en")`
   - return:
     ```python
     {"success": True, "status": "confirmation_required", "action_id": action_id,
      "expires_in_seconds": CHAT_PENDING_TTL, "preview": jsonable_encoder(preview),
      "message": format_confirmation_message(name, preview, "en"),
      "next_step": "Show this preview to the user. Call confirm_action with this action_id ONLY after the user explicitly approves; otherwise call cancel_action."}
     ```
   If `build_pending_preview` raises `ServiceError` or `ToolExecutionError`
   (`ToolExecutionError` comes from `ai.chat_tools`), return `{"success": False, "error": exc.message}`.
   It raises for records the user doesn't own.
7. Else: return `(await execute_tool(name, args, db, user, made_from=MCP_MADE_FROM))[0]`.

Also add:
```python
async def enforce_mcp_limits(user: models.User, tier: Tier) -> None:
```
It mirrors `_enforce_chat_rate_limit`, with key `f"mcp:{user.id}"`, the
two `MCP_RATE_LIMIT_*` windows and `fail_open=False`. Then, if
`settings.MCP_REQUIRES_PAID and tier == Tier.free`, raise a `ServiceError`
subclass `McpPaidTierRequired` with `status_code = 402` and message
`"MCP access requires a paid plan"`. Model it on `RateLimitedError` in
`services/rate_limits.py`. Plan 002 calls this once per `tools/call`.
Note that `call_tool` itself does not.

**Verify**: `uv run ruff check app/app/mcp_server` → `All checks passed!`

### Step 7: Tests

Create `backend/app/tests/mcp_server/test_tools.py`. Follow the
`SimpleNamespace`/`AsyncMock`/`monkeypatch` style of
`backend/app/tests/ai/test_search_transactions.py`. Patch names **in the module
where they are looked up**, for example
`monkeypatch.setattr(mcp_tools, "execute_tool", fake)` and
`monkeypatch.setattr(mcp_tools, "store_pending_action", AsyncMock(return_value=True))`.
So in `tools.py`, import these functions as module-level names
(`from app.ai.chat_tools import execute_tool, ...`).

`backend/app/tests/conftest.py:124` defines a **session-scoped autouse** `setup_database`
fixture that connects to Postgres. Override it in the new test module with a no-op, exactly as
`backend/app/tests/ai/test_turn_budget.py:21` and `backend/app/tests/api/test_chat_conversations.py:16` do:
```python
@pytest.fixture(scope="session", autouse=True)
def setup_database() -> None:
    """These unit tests do not require the integration database."""
```

Cases (one test each):
1. `list_tools()` has 26 entries, contains no `$ref`, `$defs`, `response_language` or `presentation`, and excludes `ask_user`, `render_data`, `search_web` and `submit_feedback`.
2. Every tool with `read_only=True` has `requires_confirmation(name, {}) is False`. This guards against marking a write as read-only.
3. `create_expense` with `{"amount": 5}` calls `execute_tool` once with `made_from="MCP"` and **does not** call `store_pending_action`.
4. `delete_expense` with `{"id": 7}` does **not** call `execute_tool`. It calls `store_pending_action` with `conversation_id="mcp"`, and the result has `status == "confirmation_required"` and an `action_id`. Patch `build_pending_preview` to return `{"operation": "delete"}`.
5. `bulk_delete_transactions` is confirmation-gated (same assertions as 4, minimal valid args per `BulkDeleteArgs` in `ai/tools.py:391`).
6. `manage_account` with `action="create"` executes immediately; `action="delete"` is gated.
7. `confirm_action` with an action whose `claim_pending_action` returns `None` → `success False` and `execute_tool` not called.
8. `confirm_action` where the claimed payload has `conversation_id="some-chat-id"` → refused, `execute_tool` not called.
9. `confirm_action` happy path → `execute_tool` called with `confirmed=True, made_from="MCP"` and the merged edits.
10. `confirm_action` on a stored `manage_category` update whose `edits` set `{"action": "delete"}` → refused.
11. Invalid args to `delete_expense` (`{"id": "abc"}`) → `success False`, and nothing stored.
12. `enforce_mcp_limits` raises the 402 error when `MCP_REQUIRES_PAID=True` and tier is free (patch `enforce_rate_limit` to a no-op `AsyncMock`).
13. `bulk_create_transactions` with `MCP_BULK_MAX_ITEMS + 1` expenses → `success False`, and `store_pending_action` not called.
14. `confirm_action` on a stored `update_expense` with `edits={"id": 999, "amount": 3}` → `execute_tool` receives the **stored** id, not 999.

**Verify**: `uv run pytest app/tests/mcp_server -q` → `14 passed` (or more).

The existing `app/tests/ai` suite includes modules without the no-op override (for example
`test_search_transactions.py`), so the regression gate needs the test DB:
`docker compose -f docker-compose.test.yml up -d` from the repo root. If Docker is unavailable,
run `uv run pytest app/tests/ai/test_turn_budget.py app/tests/ai/test_toolset.py -q` instead and say so in your report.

## Test plan

Covered by Step 7. The structural pattern is `backend/app/tests/ai/test_search_transactions.py`
(no DB). Regression gate: `uv run pytest app/tests/ai -q` still passes.

## Done criteria

- [ ] `uv run pytest app/tests/mcp_server -q` → ≥14 passed
- [ ] `uv run pytest app/tests/ai -q` → all pass
- [ ] `uv run ruff check app/app/mcp_server app/tests/mcp_server app/app/ai/chat_tools.py app/app/utilities/redis.py` → `All checks passed!`
- [ ] `grep -rn "import mcp\b\|from mcp " app/app/mcp_server` → no matches (no SDK dependency yet)
- [ ] `git status` shows only in-scope files modified or created (plus the two model comment edits from Step 1)
- [ ] `plans/README.md` row 001 updated

## STOP conditions

- `execute_tool`, `requires_confirmation` or `TOOL_ARG_MODELS` don't match the excerpts above.
- Any `TOOL_ARG_MODELS` model produces a self-recursive `$ref` (the inliner raises).
- `build_pending_preview` needs arguments other than `(db, user, tool_name, tool_args)`.
- An existing test in `app/tests/ai` fails after Step 2.
- `r.delete` in the Upstash client doesn't return an integer count (check `upstash_redis` docs). Report instead of switching to a different claim mechanism.

## Maintenance notes

- **New chat tools are not exposed automatically.** `MCP_TOOLS` is an explicit
  allow-list. When someone adds a tool to `TOOL_ARG_MODELS`, decide deliberately
  whether to expose it, and whether it belongs in `RISKY_CONFIRMATION_TOOLS`.
- The confirmation policy has a single source, `chat_tools.requires_confirmation`.
  Changing it changes MCP behavior too. Reviewers should check that MCP tests 3–6 still hold.
- `MCP_CONVERSATION_ID = "mcp"` is shared by all of a user's MCP sessions. Any MCP
  session of the same user can confirm an action. That is intended (it's the same
  user), and the `user_id` check in `get_pending_action` keeps users apart.
- Deferred: exposing investments (`require_investments_access` semantics) and recurring tools.
