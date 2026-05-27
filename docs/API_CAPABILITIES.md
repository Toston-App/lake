# API Capabilities Catalog

Reference for mapping chatbot user intents to backend HTTP actions. The Toston (formerly Cleverbill) API is a **FastAPI** personal finance backend.

**Base paths:** `/api/v1`, `/api/v2`, `/api/v3`

**Interactive OpenAPI:** `GET /docs` and `GET /redoc` (HTTP Basic: `DOCS_USER` / `DOCS_PASSWORD`, default `admin:root` per README). Schema: `GET /openapi.json`.

**Health (no auth):** `GET /api/v1/utils/health-check/` → `{ "status": "ok", "random_number": <int> }`

---

## Authentication

| Mechanism | Usage |
|-----------|--------|
| `POST /api/v1/login/access-token` | Email/password → local JWT (`Bearer`) |
| `Authorization: Bearer <token>` | Primary auth for API calls |
| `__session` cookie | Clerk session JWT (alternative to Bearer) |
| Local JWT | Issued by login; verified with `LOCAL_JWT_SECRET`; `sub` = DB user id |
| Clerk JWT | `iss` = `CLERK_ISSUER`; `sub` = user UUID; `azp` must be in `CLERK_AUTHORIZED_PARTIES` |
| Superuser | Routes using `get_current_active_superuser` |
| Public | Registration (`POST /users/open`), health-check, WhatsApp webhook verification |

Most financial routes require an **active user** and are **owner-scoped** (data filtered by authenticated user id). Superusers can list all expenses/incomes on some list endpoints.

---

## Shared conventions

### Date filters (`DateFilterType`)

Used on expenses, incomes, transfers, v2 data, v3 summary/charts/comparison.

| `date_filter_type` | `date` path param format | Notes |
|--------------------|--------------------------|--------|
| `date` | `YYYY-MM-DD` | Single day |
| `week` | `YYYY-MM-DD` | Week starting that date (v1 expenses: +7 days; v2 dashboard: +6 days) |
| `month` | `YYYY-MM` | Full calendar month |
| `quarter` | `YYYY-QX` (e.g. `2025-Q1`) | Q1–Q4 |
| `year` | `YYYY` | Full calendar year |
| `range` | `YYYY-MM-DD:YYYY-MM-DD` | Inclusive range; start ≤ end |

Path pattern: `GET .../{date_filter_type}/{date}`

### Pagination

- Most list endpoints: query `skip` (default 0), `limit` (default 100).
- Unified transactions: `fastapi-pagination` `Page[Transaction]` (no skip/limit on same params).

### Transaction source (`made_from`)

`Web` | `WhatsApp` | `OCR` | `Chat` (expenses and incomes).

### Account types (`AccountType`)

`Checking Accounts`, `Savings Accounts`, `Cash & Wallet`, `Stocks & Bonds`, `Crypto & Digital Assets`, `Retirement Funds`, `Loans & Mortgages`, `Credit Cards`, `Overdrafts & Negative Balances`, `Business Accounts`, `Freelance & Side Income`, `Prepaid & Gift Cards`, `Miscellaneous`.

### Common request bodies

| Model | Required fields | Optional / notable |
|-------|-----------------|-------------------|
| `ExpenseCreate` | `amount` | `date`, `account_id`, `category_id`, `subcategory_id`, `place_id`, `description`, `made_from`, `import_id` |
| `IncomeCreate` | `amount` | Same as expense (no `category_id`) |
| `TransferCreate` | `amount`, `from_acc`, `to_acc` | `date`, `description` |
| `AccountCreate` | `name` | `type`, `color` (hex), `initial_balance`, `import_id` |
| `CategoryCreate` | `name`, `color` (hex) | `description`, `icon`, `is_income`, `is_default` |
| `SubcategoryCreate` | `name`, `category_id` | `description`, `icon`, `is_default` |
| `PlaceCreate` | `name` | `is_online`, `import_id` |
| `BalanceAdjustmentCreate` | `account_id`, `new_balance` | `adjustment_date` (default today), `description` |
| `FeedbackCreate` | `message` | `sentiment` |

### Deletion responses

- Single delete: `DeletionResponse` → `{ "message": "..." }`
- v1 bulk delete expenses/incomes: `BulkDeletionResponse` → `{ "message", "deleted_ids" }`
- v2 bulk delete: `BulkDeletionsResponse` → `{ "incomes": [...], "expenses": [...] }`

---

## Infrastructure (not chatbot user actions)

| Capability | Method + path | Auth | Notes |
|------------|---------------|------|--------|
| `swagger_ui` | `GET /docs` | HTTP Basic | Swagger UI |
| `redoc` | `GET /redoc` | HTTP Basic | ReDoc |
| `openapi_schema` | `GET /openapi.json` | HTTP Basic | Generated OpenAPI |
| `health_check` | `GET /api/v1/utils/health-check/` | None | Liveness |
| `whatsapp_verify_webhook` | `GET /api/v1/whatsapp/webhook` | Query `hub.verify_token` = `WHATSAPP_VERIFY_TOKEN` | Returns `hub.challenge` |
| `whatsapp_process_webhook` | `POST /api/v1/whatsapp/webhook` | None (Meta) | AI parse, confirm/cancel, create transactions |
| `waha_webhook` | `POST /api/v1/waha/webhook` | None | WAHA messages and poll votes |

---

## API v1 — `/api/v1`

### Auth (`/login`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `login_access_token` | `POST /login/access-token` | Public | Form: `username` (email), `password` | `Token`: `{ access_token, token_type: "bearer" }` | Call first for email/password users |

### Users (`/users`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_users` | `GET /users` | Superuser | `skip`, `limit` | `list[User]` | Admin only |
| `get_current_user_profile` | `GET /users/me` | Active user | — | `UserGetMe` (`country`, etc.) | Lightweight profile |
| `update_current_user` | `PUT /users/me` | Active user | Body: optional `password`, `name`, `email`, `country`, `phone` (E.164) | `bool` | Phone hashed; MX/AR formatting rules |
| `register_user` | `POST /users/open` | Public* | Body: `use_email`, `email`, `password`, `name`, `country` | `Msg` | *Email: if `USERS_OPEN_REGISTRATION`. UUID branch: valid Clerk Bearer/cookie required; body UUID not trusted |
| `get_user_by_id` | `GET /users/{user_id}` | Active user (superuser enforced) | `user_id` | `User` | Admin |
| `admin_update_user` | `PUT /users/{user_id}` | Superuser | `UserUpdate` | `User` | Admin |
| `delete_user` | `DELETE /users/{id}` | Superuser | `id` | `User` | Cannot delete self |
| `set_default_whatsapp_account` | `PUT /users/me/default-account` | Active user | Body: `account_id` | `Account` | For WhatsApp default |
| `get_default_whatsapp_account` | `GET /users/me/default-account` | Active user | — | `Account` | 404 if unset |
| `clear_default_whatsapp_account` | `DELETE /users/me/default-account` | Active user | — | `bool` | |

### Accounts (`/accounts`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_accounts` | `GET /accounts` | Active user | `skip`, `limit` | `list[Account]` | Needed before creating transactions |
| `create_account` | `POST /accounts` | Active user | `AccountCreate` | `Account` | |
| `get_account` | `GET /accounts/{id}` | Active user | `id` | `Account` | |
| `update_account` | `PUT /accounts/{id}` | Active user | `AccountUpdate` | `Account` | |
| `delete_account` | `DELETE /accounts/{id}` | Active user | `id` | `DeletionResponse` | |

### Balance adjustments (`/balance-adjustments`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `create_balance_adjustment` | `POST /balance-adjustments` | Active user | `BalanceAdjustmentCreate` | `BalanceAdjustment` | Sets account balance; updates user totals |
| `list_balance_adjustments` | `GET /balance-adjustments` | Active user | — | `list[BalanceAdjustment]` | |
| `list_balance_adjustments_by_account` | `GET /balance-adjustments/account/{account_id}` | Active user | `account_id` | `list[BalanceAdjustment]` | |
| `get_balance_adjustment` | `GET /balance-adjustments/{id}` | Active user | `id` | `BalanceAdjustment` | |

### Expenses (`/expenses`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_expenses` | `GET /expenses/getAll` | Active user | `skip`, `limit` | `list[Expense]` | Superuser sees all |
| `list_expenses_by_date` | `GET /expenses/{date_filter_type}/{date}` | Active user | Date filter | `list[Expense]` | |
| `create_expense` | `POST /expenses` | Active user | `ExpenseCreate` | `Expense` | Prefer when adding one expense |
| `create_expenses_bulk` | `POST /expenses/bulk` | Active user | `list[ExpenseCreate]` | `list[Expense]` | Multiple expenses |
| `get_expense` | `GET /expenses/{id}` | Active user | `id` | `Expense` | |
| `update_expense` | `PUT /expenses/{id}` | Active user | `ExpenseUpdate` | `Expense` | |
| `delete_expense` | `DELETE /expenses/{id}` | Active user | `id` | `DeletionResponse` | |
| `delete_expenses_bulk` | `DELETE /expenses/bulk/{ids}` | Active user | `ids` comma-separated | `BulkDeletionResponse` | e.g. `1,2,3` |

### Incomes (`/incomes`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_incomes` | `GET /incomes/getAll` | Active user | `skip`, `limit` | `list[Income]` | Same pattern as expenses |
| `list_incomes_by_date` | `GET /incomes/{date_filter_type}/{date}` | Active user | Date filter | `list[Income]` | |
| `create_income` | `POST /incomes` | Active user | `IncomeCreate` | `Income` | |
| `create_incomes_bulk` | `POST /incomes/bulk` | Active user | `list[IncomeCreate]` | `list[Income]` | |
| `get_income` | `GET /incomes/{id}` | Active user | `id` | `Income` | |
| `update_income` | `PUT /incomes/{id}` | Active user | `IncomeUpdate` | `Income` | |
| `delete_income` | `DELETE /incomes/{id}` | Active user | `id` | `DeletionResponse` | |
| `delete_incomes_bulk` | `DELETE /incomes/bulk/{ids}` | Active user | `ids` comma-separated | `BulkDeletionResponse` | |

### Transfers (`/transfers`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_transfers` | `GET /transfers/getAll` | Active user | `skip`, `limit` | `list[Transfer]` | |
| `list_transfers_by_date` | `GET /transfers/{date_filter_type}/{date}` | Active user | Date filter | `list[Transfer]` | |
| `create_transfer` | `POST /transfers` | Active user | `TransferCreate` | `Transfer` | Between two accounts |
| `get_transfer` | `GET /transfers/{id}` | Active user | `id` | `Transfer` | |
| `update_transfer` | `PUT /transfers/{id}` | Active user | `TransferUpdate` | `Transfer` | |
| `delete_transfer` | `DELETE /transfers/{id}` | Active user | `id` | `DeletionResponse` | |

### Transactions (`/transactions`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `search_transactions` | `GET /transactions` | Active user | Query: `order` (`asc`\|`desc`), `search`, `amount`, `amount_operator` (`equal`\|`less`\|`greater`), `start_date`, `end_date`, `accounts[]`, `categories[]`, `places[]`, `transaction_type[]` (`expense`\|`income`\|`transfer`) | `Page[Transaction]` | **Prefer** for unified search/filter across types |

### Places (`/places`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_places` | `GET /places` | Active user | `skip`, `limit` | `list[Place]` | |
| `create_place` | `POST /places` | Active user | `PlaceCreate` | `Place` | |
| `get_place` | `GET /places/{id}` | Active user | `id` | `Place` | |
| `update_place` | `PUT /places/{id}` | Active user | `PlaceUpdate` | `Place` | |
| `delete_place` | `DELETE /places/{id}` | Active user | `id` | `DeletionResponse` | |

### Categories (`/categories`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_categories` | `GET /categories` | Active user | `skip`, `limit` | `list[Category]` | Includes nested subcategories |
| `create_category` | `POST /categories` | Active user | `CategoryCreate` | `Category` | |
| `get_category` | `GET /categories/{id}` | Active user | `id` | `Category` | |
| `update_category` | `PUT /categories/{id}` | Active user | `CategoryUpdate` | `Category` | |
| `delete_category` | `DELETE /categories/{id}` | Active user | `id` | `DeletionResponse` | |

### Subcategories (`/subcategories`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_subcategories` | `GET /subcategories` | Active user | `skip`, `limit` | `list[Subcategory]` | |
| `create_subcategory` | `POST /subcategories` | Active user | `SubcategoryCreate` | `Subcategory` | Requires `category_id` |
| `get_subcategory` | `GET /subcategories/{id}` | Active user | `id` | `Subcategory` | |
| `update_subcategory` | `PUT /subcategories/{id}` | Active user | `SubcategoryUpdate` | `Subcategory` | |
| `delete_subcategory` | `DELETE /subcategories/{id}` | Active user | `id` | `DeletionResponse` | |

### Import (`/import`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `import_bluecoins_csv` | `POST /import/bluecoins` | Active user | Multipart: `csv_file` | Dict: `message`, `total_transactions_imported`, `expenses_imported`, `incomes_imported`, `accounts_created`, `sites_created`, `unmatched_categories`, `total_rows_processed` | Bluecoins column mapping |
| `import_generic_csv` | `POST /import/csv` | Active user | Multipart: `csv_file` | Same shape | Generic CSV with `Site` column |

### Feedback (`/feedback`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `submit_feedback` | `POST /feedback` | Active user | `FeedbackCreate`: `message`, optional `sentiment` | `bool` | Also notifies Telegram |

### Recap (`/recap`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `get_annual_recap` | `GET /recap/{year}` | Active user | `year` (path; **handler forces 2025**) | `UserRecap` or `RecapStatusResponse` | Redis-cached; 404 if missing |
| `get_recap_status` | `GET /recap/{year}/status` | Active user | `year` (2020–2030) | `RecapStatusResponse` | `status`, optional `message` |

### Demo data (`/demo-data`) — superuser only

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `generate_demo_data` | `POST /demo-data/generate-demo-data` | Superuser | Body: `user_id`, `num_transactions` (1–5000), optional `start_date_str`, `end_date_str` | `Msg` (201) | Do not expose to end users |
| `delete_user_financial_data` | `POST /demo-data/delete-user-financial-data` | Superuser | Body: `user_id` | `Msg` (200) | Wipes user financial data |

### Utils (`/utils`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `send_test_email` | `POST /utils/test-email/` | Superuser | `email_to` (EmailStr) | `Msg` (201) | Admin/dev |

---

## API v2 — `/api/v2`

### Data (`/data`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `list_expenses_legacy` | `GET /data/getAll` | Active user | `skip`, `limit` | `list[Data]` | Legacy alias for expense list |
| `get_dashboard_bundle` | `GET /data/{date_filter_type}/{date}` | Active user | Date filter | Object: `country`, `accounts`, `balance` (`total`, `income`, `outcome`), `incomes`, `expenses`, `transfers`; may include `charts` when empty | Single call for dashboard; v3 is leaner for summary/charts |

### Bulk (`/bulk`)

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `bulk_delete_incomes_expenses` | `DELETE /bulk` | Active user | `BulkDelete`: `{ incomes?: int[], expenses?: int[] }` | `BulkDeletionsResponse` | Deletes both types in one request |
| `bulk_create_incomes_expenses` | `POST /bulk` | Active user | `BulkCreate`: `{ incomes?: IncomeCreate[], expenses?: ExpenseCreate[] }` | `BulkCreationsResponse` | Prefer over separate v1 bulk calls when mixing types |

---

## API v3 — `/api/v3`

Redis-cached dashboard endpoints. Shared path params: `date_filter_type`, `date`. Optional query: `account_id` (summary and charts).

| Capability | Method + path | Auth | Inputs | Returns | Chatbot notes |
|------------|---------------|------|--------|---------|---------------|
| `get_period_summary` | `GET /summary/{date_filter_type}/{date}` | Active user | Optional `account_id` | `SummaryResponse`: `country`, `balance`, `period_income`, `period_expenses`, `period_net` | **Prefer** for totals |
| `get_period_charts` | `GET /charts/{date_filter_type}/{date}` | Active user | Optional `account_id` | `ChartsResponse`: `net`, `income_vs_expense`, `categories` | **Prefer** for chart data |

---

## Disabled / not mounted routes

These exist in code but are **not** registered on the live app (or are commented out). Do not use until routers are enabled.

| Capability | Method + path (if mounted) | File | Reason |
|------------|----------------------------|------|--------|
| `ocr_receipt` | `POST /api/v1/ai/ocr` | `api_v1/endpoints/ai.py` | Router commented in `api_v1/api.py` |
| `list_items` | `GET /api/v1/items/` | `api_v1/endpoints/items.py` | Router never included |
| `create_item` | `POST /api/v1/items/` | `api_v1/endpoints/items.py` | Router never included |
| `get_item` | `GET /api/v1/items/{id}` | `api_v1/endpoints/items.py` | Router never included |
| `update_item` | `PUT /api/v1/items/{id}` | `api_v1/endpoints/items.py` | Router never included |
| `delete_item` | `DELETE /api/v1/items/{id}` | `api_v1/endpoints/items.py` | Router never included |
| `get_period_comparison` | `GET /api/v3/comparison/{date_filter_type}/{date}` | `api_v3/endpoints/comparison.py` | Router commented in `api_v3/api.py` |
| `test_token` | `POST /api/v1/login/test-token` | `api_v1/endpoints/login.py` | Commented out |
| `password_recovery` | `POST /api/v1/password-recovery/{email}` | `api_v1/endpoints/login.py` | Commented out |
| `reset_password` | `POST /api/v1/reset-password` | `api_v1/endpoints/login.py` | Commented out |
| `create_expense_legacy` | `POST /api/v2/data/` | `api_v2/endpoints/data.py` | Commented out (dead CRUD stub) |
| `get_expense_legacy` | `GET /api/v2/data/{id}` | `api_v2/endpoints/data.py` | Commented out |
| `update_expense_legacy` | `PUT /api/v2/data/{id}` | `api_v2/endpoints/data.py` | Commented out |
| `delete_expense_legacy` | `DELETE /api/v2/data/{id}` | `api_v2/endpoints/data.py` | Commented out |

**Disabled OCR** (`POST /api/v1/ai/ocr` when enabled): multipart `image`; auth active user; returns parsed transaction fields from receipt image.

**Disabled comparison** (`GET /api/v3/comparison/...` when enabled): `ComparisonResponse` with `accounts_growth` (period-over-period % by account).

---

## Chat API — `/api/v1/chat`

Authenticated natural-language interface (OpenRouter tool-calling agent). Executes the same owner-scoped CRUD and v3 dashboard logic as the REST API. Conversation history and pending delete confirmations are stored in Redis.

**Feature flag:** `CHAT_ENABLED` (default `true`). Returns `503` when disabled.

**Config:** `CHAT_MODEL` (optional, defaults to `OPENROUTER_MODEL`), `CHAT_MAX_TOOL_ROUNDS` (default `8`), `CHAT_CONVERSATION_TTL_SECONDS` (default 7 days).

| Capability | Method + path | Auth | Inputs | Returns |
|------------|---------------|------|--------|---------|
| `send_chat_message` | `POST /chat/messages` | Active user | `ChatMessageRequest`: `message`, optional `conversation_id` | `ChatMessageResponse`: `conversation_id`, `reply`, `actions[]`, optional `pending_confirmation` |
| `list_chat_conversations` | `GET /chat/conversations` | Active user | — | `list[ConversationSummary]` |
| `get_chat_conversation` | `GET /chat/conversations/{id}` | Active user | `id` | `ConversationDetail` with `messages[]` |
| `delete_chat_conversation` | `DELETE /chat/conversations/{id}` | Active user | `id` | `{ "ok": true }` |
| `confirm_chat_action` | `POST /chat/conversations/{id}/confirm` | Active user | `ChatConfirmRequest`: `action_id` | `ChatMessageResponse` after executing pending destructive action |

### Agent tools (in-process, not HTTP)

`get_user_profile`, `list_accounts`, `manage_account`, `list_categories`, `manage_category`, `manage_subcategory`, `list_places`, `manage_place`, `create_expense`, `update_expense`, `delete_expense`, `create_income`, `update_income`, `delete_income`, `create_transfer`, `update_transfer`, `delete_transfer`, `search_transactions`, `get_period_summary`, `get_period_charts`, `bulk_create_transactions`, `bulk_delete_transactions`, `create_balance_adjustment`, `list_balance_adjustments`, `submit_feedback`.

Creates from chat set `made_from: "Chat"` on expenses/incomes.

### Destructive actions (confirmation required)

`delete_expense`, `delete_income`, `delete_transfer`, `bulk_delete_transactions`, and `manage_*` with `action: "delete"` return `pending_confirmation` until the client calls `confirm_chat_action`.

### Chat exclusions (no tool)

- CSV import (multipart)
- OCR (`/ai/ocr` disabled)
- Webhooks, docs, superuser/demo-data routes

---

## Chatbot mapping notes

### Typical user flows

1. **Authenticate:** `login_access_token` or existing Clerk session (Bearer / `__session`).
2. **Bootstrap metadata:** `list_accounts`, `list_categories`, `list_places` (and subcategories as needed).
3. **Write transactions:** `create_expense`, `create_income`, or `create_transfer` (set `made_from: "Chat"` for web chat, `"WhatsApp"` for WhatsApp).
4. **Read / search:** `search_transactions` for filtered unified feed; v3 `get_period_summary` / `get_period_charts` for dashboard questions.
5. **Bulk:** v2 `bulk_create_incomes_expenses` / `bulk_delete_incomes_expenses` when handling many rows at once.

### API version guidance

| Need | Use |
|------|-----|
| CRUD on entities | **v1** |
| Dashboard totals | **v3** summary |
| Chart payloads | **v3** charts |
| Full dashboard bundle (legacy) | **v2** data |
| Mixed bulk create/delete | **v2** bulk |

### Do not expose as chatbot tools

- Documentation endpoints (`/docs`, `/redoc`, `/openapi.json`)
- WhatsApp / WAHA webhooks (server-to-server)
- Superuser: `list_users`, `delete_user`, `admin_update_user`, demo-data, `send_test_email`

### Registration

- `POST /users/open` with `use_email: true` — email/password signup (gated by `USERS_OPEN_REGISTRATION`).
- `use_email: false` — creates local row for Clerk user; requires valid Clerk token; do not trust client-supplied UUID.

### Maintenance

Re-sync this document when routers change in:

- `backend/app/app/api/api_v1/api.py`
- `backend/app/app/api/api_v2/api.py`
- `backend/app/app/api/api_v3/api.py`
- `backend/app/app/main.py`

Optional follow-up: append generated route list from `GET /openapi.json` after auth.

---

## Route index (verification)

All `@router` handlers in `backend/app/app/api/**/endpoints/*.py` are listed above as either **mounted** (v1/v2/v3 sections) or **disabled**. App-level routes: `main.py` (docs, health).

**Mounted router map:**

| Prefix | Module |
|--------|--------|
| `/api/v1/login` | `login` |
| `/api/v1/users` | `users` |
| `/api/v1/utils` | `utils` (+ health in `main.py`) |
| `/api/v1/demo-data` | `demo_data` |
| `/api/v1/places` | `places` |
| `/api/v1/expenses` | `expenses` |
| `/api/v1/incomes` | `incomes` |
| `/api/v1/transfers` | `transfers` |
| `/api/v1/transactions` | `transactions` |
| `/api/v1/accounts` | `accounts` |
| `/api/v1/balance-adjustments` | `balance_adjustments` |
| `/api/v1/categories` | `categories` |
| `/api/v1/subcategories` | `subcategories` |
| `/api/v1/import` | `imports` |
| `/api/v1/whatsapp` | `whatsapp` |
| `/api/v1/waha` | `waha` |
| `/api/v1/feedback` | `feedback` |
| `/api/v1/recap` | `recap` |
| `/api/v1/chat` | `chat` |
| `/api/v2/data` | `data` |
| `/api/v2/bulk` | `bulk` |
| `/api/v3/summary` | `summary` |
| `/api/v3/charts` | `charts` |
