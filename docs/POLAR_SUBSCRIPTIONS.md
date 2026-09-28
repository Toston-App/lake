# Polar Subscriptions

Operator guide for the [Polar.sh](https://polar.sh) billing integration that powers Toston Chat tiers, usage quotas, and feature gates.

Polar is the **billing provider**. Lake owns **entitlements**, **usage counting**, and **tool/feature gating**. A successful checkout alone does not upgrade a user — Polar **webhooks** are the source of truth for paid state.

For chat API behavior (including HTTP **402** when over quota), see [CHAT_API.md](./CHAT_API.md).

**Reference client:** [`frontend/chat/`](../frontend/chat/) — `src/api/subscriptions.ts`, `src/hooks/useSubscription.ts`.

---

## Overview

| Property | Value |
|----------|-------|
| Billing provider | Polar.sh (SDK: `polar-sdk`) |
| Master switch | `SUBSCRIPTIONS_ENABLED` |
| Subscription API | `{host}/api/v1/subscriptions/*` (authenticated) |
| Webhook | `{host}/api/v1/polar/webhook` (public, signature-verified) |
| Success redirect | `{POLAR_SUCCESS_URL_BASE or SERVER_HOST}/account?checkout=success` |
| Servers | `sandbox` → Polar sandbox API; `production` → live API |

When subscriptions are **disabled**, every user gets `SUBSCRIPTIONS_DEV_DEFAULT_TIER` (default `tier2` for local full-chat). When `POLAR_SERVER=production`, that default is forced to **`free`** so a misconfigured production deploy never grants a paid tier without Polar.

### Code map

| Area | Path |
|------|------|
| Config / flags | `backend/app/app/core/config.py` |
| Tier catalog | `backend/app/app/core/tiers.py` |
| Polar SDK wrapper | `backend/app/app/services/polar.py` |
| Entitlements / quota | `backend/app/app/services/entitlements.py` |
| Subscription HTTP API | `backend/app/app/api/api_v1/endpoints/subscriptions.py` |
| Webhook receiver | `backend/app/app/api/api_v1/endpoints/polar.py` |
| CRUD / idempotency | `backend/app/app/crud/crud_subscription.py` |
| Models | `backend/app/app/models/subscription.py`, `polar_webhook_event.py` |
| Entitlement cache | `backend/app/app/utilities/redis.py` (`chat:ent:{user_id}`) |
| Feature gating in agent | `backend/app/app/ai/chat_tools.py` |

---

## Configuration

### 1. Environment variables

Copy the Polar block from `.env.develop` into `.env` and fill values for the environment you are targeting.

| Variable | Required when enabled | Default | Purpose |
|----------|----------------------|---------|---------|
| `SUBSCRIPTIONS_ENABLED` | — | auto | Master switch. See [Enable rules](#enable-rules) |
| `POLAR_ACCESS_TOKEN` | yes | — | Org-scoped access token from the Polar dashboard |
| `POLAR_WEBHOOK_SECRET` | yes | — | Signing secret for the webhook endpoint |
| `POLAR_ORGANIZATION_ID` | yes | — | Polar organization id |
| `POLAR_SERVER` | no | `sandbox` | `sandbox` or `production` |
| `POLAR_SUCCESS_URL_BASE` | no | falls back to `SERVER_HOST` | Public base URL used to build checkout `success_url` |
| `POLAR_PRODUCT_TIER1_ID` | for checkout | — | Polar product id for tier 1 |
| `POLAR_PRODUCT_TIER2_ID` | for checkout | — | Polar product id for tier 2 |
| `POLAR_WEBHOOK_MAX_BYTES` | no | `262144` | Maximum raw webhook body size |
| `CLERK_SECRET_KEY` | for UUID-only Clerk users | — | Clerk Backend API secret used to retrieve the user's primary email/name before creating a Polar customer |
| `CHAT_TURN_RESERVATION_TTL_SECONDS` | no | `900` | Maximum age of an in-progress quota reservation |
| `SUBSCRIPTION_RENEWAL_GRACE_HOURS` | no | `72` | Paid access lapses this long after `current_period_end` unless a renewal webhook moved the period |
| `SUBSCRIPTIONS_DEV_DEFAULT_TIER` | no | `tier2` | Tier when subscriptions are disabled. Forced to `free` if `POLAR_SERVER=production` or `ENVIRONMENT=production` |
| `ENVIRONMENT` | no | `production` | Anything other than `production`/`prod` (e.g. `development`, `staging`, `test`) is non-production. See [Production fail-closed](#production-fail-closed) |

Product ids are optional for sandbox app boot, but both are required and must be unique in production. Unknown webhook product ids never grant a tier; see [Tier catalog](#tier-catalog) for how their cancellations are still applied.

### Production fail-closed

`ENVIRONMENT` defaults to `production`, so a deployment that forgets it is treated as production:

- Subscriptions disabled → everyone is `free`, whatever `SUBSCRIPTIONS_DEV_DEFAULT_TIER` or `POLAR_SERVER` say.
- Subscriptions enabled with `POLAR_SERVER=sandbox` → startup fails. Sandbox test cards would otherwise unlock paid tiers.

Set `ENVIRONMENT=development` locally and `ENVIRONMENT=staging` for a sandbox-backed staging deployment.

### Enable rules

`SUBSCRIPTIONS_ENABLED` is validated at settings load (`config.py`):

| Setting | Result |
|---------|--------|
| Unset / empty | **Auto-enable** if `POLAR_ACCESS_TOKEN` is non-empty; otherwise disabled |
| Explicit `false` / `0` / `no` / `off` | Always **disabled** (even if a token is present) |
| Explicit `true` / `1` / `yes` / `on` | **Enabled** only if token + webhook secret + org id are all set |

If enabled and any of `POLAR_ACCESS_TOKEN`, `POLAR_WEBHOOK_SECRET`, or `POLAR_ORGANIZATION_ID` is missing, the process **fails at startup** with a clear validation error.

### 2. Polar dashboard setup

Do this once per environment (sandbox org for local/staging, production org for live).

1. **Create an organization** in [Polar](https://polar.sh) (use **sandbox** for non-production).
2. **Create two recurring products** — one per paying tier (`tier1`, `tier2`). Prices are cosmetic in our UI (`price_hint` in code); Polar is the system of record for charging.
3. **Create an organization access token** → set `POLAR_ACCESS_TOKEN`.
4. **Copy the organization id** → set `POLAR_ORGANIZATION_ID`.
5. **Create a webhook endpoint** pointing at:

   ```text
   https://<public-backend-host>/api/v1/polar/webhook
   ```

   Locally, expose the backend with a tunnel (ngrok, Cloudflare Tunnel, etc.) so Polar can reach it or use Polar cli:

    ```bash
    polar listen http://localhost:8888/api/v1/polar/webhook
    ```

6. **Subscribe the webhook** at least to:

   | Event | Used for |
   |-------|----------|
   | `subscription.created` | New subscription row |
   | `subscription.updated` | Status / period changes |
   | `subscription.active` | Active paid state |
   | `subscription.canceled` | Scheduled cancellation; access continues to `ends_at` |
   | `subscription.uncanceled` | Restore after cancel |
   | `subscription.revoked` | Hard revoke → immediate access loss |
   | `subscription.past_due` | Payment failure state |
   | `customer.created` | Bind `polar_customer_id` via `external_id` |
   | `customer.updated` | Same bind path |

   Other event types (checkout, order, benefit grant, …) are **acknowledged and ignored** so Polar does not retry forever.

7. **Copy the webhook secret** → set `POLAR_WEBHOOK_SECRET`.
8. **Paste product ids** into `POLAR_PRODUCT_TIER1_ID` / `TIER2`.
9. Set `POLAR_SERVER` to `sandbox` or `production` to match the org you used.
10. Set `POLAR_SUCCESS_URL_BASE` to the **public origin users hit for the chat app** if it differs from `SERVER_HOST` (checkout returns to `{base}/account?checkout=success`).
11. Disable **multiple subscriptions per customer** in Polar. Application plan changes update the one existing subscription.
12. Disable **plan changes in the customer portal**, and keep only the two mapped products in the org. A portal switch to an unmapped product can't be mapped to a tier.

### 3. Database migrations

Subscription tables ship as Alembic revisions:

| Revision | What it adds |
|----------|----------------|
| `a1b3c5d7e9f0` | `user_subscription`, `user.polar_customer_id` |
| `b2c4d6e8f0a1` | Unique customer binding, `last_event_at`, `polar_webhook_event` idempotency ledger |
| `c3d9e7f1a2b4` | Billing usage and chat observability foundations |
| `d5e7f9a1b3c4` | Pending plan-change fields and quota lookup index |
| `e6f8a1c2d4b5` | Polar cancellation/end fields and same-timestamp event priority |

After pull:

```console
$ docker-compose exec backend bash
$ alembic upgrade head
```

### 4. Environment checklists

#### Local development (no billing)

```bash
SUBSCRIPTIONS_ENABLED=false
SUBSCRIPTIONS_DEV_DEFAULT_TIER=tier2   # full features locally
POLAR_SERVER=sandbox
```

Leave Polar credentials empty. Checkout/portal return **503**. Chat uses the dev default tier.

#### Local / staging with real sandbox billing

```bash
SUBSCRIPTIONS_ENABLED=true
POLAR_SERVER=sandbox
POLAR_ACCESS_TOKEN=polar_oat_...
POLAR_WEBHOOK_SECRET=...
POLAR_ORGANIZATION_ID=...
POLAR_PRODUCT_TIER1_ID=...
POLAR_PRODUCT_TIER2_ID=...
POLAR_SUCCESS_URL_BASE=https://<your-tunnel-or-staging-host>
```

Webhook URL must hit **this** backend instance (same Postgres + Redis as chat).

#### Production

```bash
ENVIRONMENT=production
SUBSCRIPTIONS_ENABLED=true
POLAR_SERVER=production
# production OAT, webhook secret, org id, product ids only
# never reuse sandbox tokens or product ids
CHAT_ALLOWED_USER_UUIDS=*   # or an explicit beta allowlist
```

If Polar is off in production, everyone is **free** (`SUBSCRIPTIONS_DEV_DEFAULT_TIER` is forced to `free`).

---

## How it works

### Tier catalog

Defined in `backend/app/app/core/tiers.py` (numbers are tunable starting defaults):

| Tier | Message limit / mo | Token limit / mo | Features (examples) |
|------|--------------------|------------------|---------------------|
| `free` | 10 | 20_000 | none |
| `tier1` (Basic, $4.99/mo) | 100 | 600_000 | `charts` |
| `tier2` (Premium, $14.99/mo) | unlimited | 5_000_000 (fair use) | all (`search_web`, `charts`, `advanced_analytics`, `bulk_operations`, `priority_support`, …) |

- `polar_product_id` is injected from env via `configure_product_ids`.
- Checkout uses product id → Polar.
- Webhooks reverse-map product id → tier via `tier_for_product`. An unknown product never grants or changes a tier. Snapshots that end or suspend access (cancellation, revocation, `past_due`, any `ends_at`/`ended_at`) are still applied to the row when it tracks the same `polar_subscription_id`, keeping the row's tier. Otherwise retiring or rotating a product id would leave its subscribers paid forever.

Feature keys gate tools and system-prompt sections in `ai/chat_tools.py` (`has_feature`).

### Customer identity

| Concept | Rule |
|---------|------|
| Polar `external_id` | `User.uuid` (Clerk) when present, else `str(User.id)` |
| Binding | Stored on `User.polar_customer_id` and `UserSubscription.polar_customer_id` |
| Uniqueness | One Polar customer id maps to **one** local user (partial unique indexes) |

Checkout calls `ensure_customer`: look up by `external_id`, create if missing, then bind. If that customer is already linked to another user → **409**.

### Checkout flow

```text
Chat UI → POST /api/v1/subscriptions/checkout { "tier": "tier1" }
       → ensure Polar customer (create/bind)
       → polar.checkouts.create (product + customer + success_url)
       → { url, checkout_id }
Chat UI → redirect browser to Polar hosted checkout
User pays
Polar → webhooks → user_subscription upsert + Redis entitlement invalidate
Chat UI → GET /api/v1/subscriptions/me reflects new tier
```

Important: landing on `/account?checkout=success` is **UX only**. Entitlement updates when the webhook is processed.

### Customer portal flow

```text
POST /api/v1/subscriptions/portal
  → requires polar_customer_id
  → polar.customer_sessions.create (external_customer_id)
  → { url } → redirect to Polar portal
```

`GET /me` sets `can_manage=true` whenever the user has a bound Polar customer, including `past_due` and canceled recovery states.

### Webhook pipeline

```text
POST /api/v1/polar/webhook  (no auth header; signature only)
  │
  ├─ SUBSCRIPTIONS_ENABLED=false → 202 (silent ack, no retries)
  ├─ body exceeds configured limit → 413
  ├─ missing secret / no SDK → 503
  ├─ invalid signature → 403
  ├─ valid signature + webhook-id already claimed → 202
  ├─ unknown event type (valid signature) → mark id, 202
  ├─ handler error → 500 (Polar retries; do NOT mark id processed)
  └─ success → mark webhook-id processed, 202
```

#### User resolution on subscription events

1. `User.polar_customer_id == payload.customer_id` (fast path)
2. Else `UserSubscription.polar_customer_id`
3. Else Polar API `customers.get` → `external_id` → local user by uuid or numeric id; then bind

If no local user can be mapped permanently, the event is logged as `unresolved_customer` and acknowledged without mutation. Transient Polar lookup failures return 500 so the delivery is retried.

#### Idempotence

Polar (and any HTTP webhook path) can **retry** or **deliver twice**. The integration is designed so replaying the same delivery, or applying an older event after a newer one, does not corrupt subscription state.

There are **two independent layers**:

```text
Layer 1 — delivery dedupe (same HTTP delivery)
  webhook-id header → polar_webhook_event PK
  already seen → 202, no re-handle

Layer 2 — state ordering (different deliveries, out of order)
  event time → user_subscription.last_event_at
  older than last applied → skip upsert (no overwrite)
  equal timestamp → deterministic fail-closed lifecycle priority
```

##### Layer 1: delivery idempotence (`webhook-id`)

| Piece | Detail |
|-------|--------|
| Header | Standard Webhooks `webhook-id` (unique per Polar delivery attempt identity) |
| Table | `polar_webhook_event` — PK = `webhook_id` |
| Claim | Inserted with `ON CONFLICT DO NOTHING RETURNING` in the same transaction as the subscription mutation; only the transaction that claims it handles the event |
| Commit | Claim and mutation commit together. A failed handler rolls both back, returns **500**, and leaves the delivery retryable |
| Insert | `ON CONFLICT DO NOTHING` so concurrent marks cannot double-insert |

Flow:

```text
receive webhook
  → if webhook-id known → stop (idempotent no-op)
  → validate signature
  → handle_polar_event (may upsert subscription)
  → on success: INSERT polar_webhook_event(webhook_id)
  → on failure: 500, leave ledger empty → safe retry
```

**Implications:**

- Replaying the **same** delivery after success is a no-op at the edge.
- A delivery that failed mid-handler is **not** considered done; retry re-runs the full handler (see layer 2 for state safety).
- If Polar omits `webhook-id`, delivery dedupe is skipped for that request (layer 2 still applies). Unusual for Standard Webhooks, but worth knowing.

##### Layer 2: state idempotence / ordering (`last_event_at`)

Subscription writes go through `upsert_from_webhook` (`crud_subscription.py`):

1. Derive `event_at` from the webhook's top-level `timestamp`, with payload/header fallbacks.
2. Claim `webhook-id` and apply the state change in one database transaction.
3. Use `INSERT … ON CONFLICT (user_id) DO UPDATE` with `last_event_at` and a deterministic lifecycle priority. Older snapshots cannot overwrite current state; for equal timestamps, access-losing events win over access-granting events.

So:

| Situation | Result |
|-----------|--------|
| Same logical sub state applied twice with **different** delivery ids and equal timestamps | Second write is skipped |
| Older event arrives after a newer one was applied | Skipped (`subscription_event_skipped` / `stale_event`) |
| Handler failure | The transaction rolls back both the event claim and state change, then Polar retries |
| Distinct events with the **same** `event_at` | Deterministic fail-closed priority wins (`revoked` outranks `active`) |

##### Other “safe to retry” paths

| Path | Behavior |
|------|----------|
| `ensure_customer` | Lookup by `external_id` first; create only if missing; bind is refused if customer already owned by another user |
| `set_polar_customer_id` | No-op-ish when same user; **ValueError** if another user owns the id |
| `ensure_free_row` | `ON CONFLICT DO NOTHING` on `user_id` |
| Entitlement cache | Redis invalidation runs after the DB commit. Failure returns 500; duplicate/stale replay reruns cache repair without regressing DB state |
| Checkout / portal create | **Not** fully idempotent: each call can create a **new** Polar checkout/session URL. Safe to call again, but you get a new session, not the same id |

##### What is *not* guaranteed

- **Exactly-once side effects outside this DB** — we only control local rows + Redis invalidation. Polar’s own charges are Polar’s domain.
- **Exactly-once calls to Polar APIs** — checkout and plan-change requests remain external operations; local webhook state is atomic and idempotent.

##### Operator checklist

| Symptom | Idempotence angle |
|---------|-------------------|
| Polar retries after 500 | Expected until success; ledger empty until then |
| Logs show `outcome=duplicate` | Same `webhook-id` already applied — ignore |
| Tier flapped then stabilized | Out-of-order deliveries; `last_event_at` should leave newest state |
| Manual re-send from Polar dashboard | Safe if same id (dedupe) or newer snapshot (upsert) |

#### Status → effective tier

| Stored `status` | Effective tier used by chat |
|-----------------|-----------------------------|
| `active`, `trialing` without an elapsed end or incomplete scheduled-cancel data | Row’s `tier` (from product mapping) |
| Scheduled cancellation (`cancel_at_period_end`, future `ends_at`) | Row’s tier until, but not including, `ends_at` |
| `revoked`, `unpaid`, elapsed `ends_at`/`ended_at` | **`free`** immediately |
| `past_due`, `incomplete`, unknown status, canceled without an end | **`free`** (fail closed) |
| Any status more than `SUBSCRIPTION_RENEWAL_GRACE_HOURS` past `current_period_end` | **`free`**: the renewal webhook never arrived |

The row may keep historical `tier` for display/debug; entitlements only grant paid features for active/trialing.

### Entitlements, usage, and enforcement

Resolution (`services/entitlements.py`):

1. If subscriptions disabled → dev default tier.
2. Else Redis `chat:ent:{user_id}` (TTL **1 hour**).
3. On miss → load `user_subscription`, compute effective tier, cache.

Usage window:

- Paying active sub with `current_period_start` → that timestamp to `current_period_end`.
- Free / inactive → first day of current UTC calendar month.

Usage sources: aggregate `chat_obs_turns` for the user since period start. Messages exclude server errors and expired `in_progress` reservations; a turn whose client disconnected mid-stream is stored as `interrupted` and counts. Tokens count for every turn, including errors, because the model usage was paid for. A failed confirmation never refunds the turn that proposed it. Before model execution, the backend holds a per-user PostgreSQL advisory lock, checks usage, and inserts an `in_progress` reservation atomically. Billing reservations remain enabled even when optional observability is disabled.

Limits from the tier config:

- `messages_used >= monthly_message_limit` → deny (`message_limit_reached`)
- `tokens_used >= monthly_token_limit` → deny (`token_limit_reached`)

Chat endpoints reserve quota and return **HTTP 402** when denied or **503** when billing usage cannot be recorded safely. Independently of tiers, `POST /chat` is rate limited per user (`CHAT_RATE_LIMIT_PER_MINUTE`, `CHAT_RATE_LIMIT_PER_DAY`, **429** with `Retry-After`), and each turn is capped at `CHAT_MAX_TOOL_ROUNDS` + 1 model calls, `CHAT_MAX_TOKENS_PER_TURN` total tokens and `CHAT_MAX_OUTPUT_TOKENS` per response.

Checkout and plan changes require chat access (`CHAT_ALLOWED_USER_UUIDS`, `*` for everyone), so nobody can pay for a plan they can't use. Successful subscription webhooks invalidate Redis after the database commit so the next turn sees the new tier without waiting for TTL. An invalidation failure returns HTTP 500; Polar's duplicate retry replays the idempotent handler and repairs the cache.

### Reconciliation

The reconciliation command compares local rows and entitlement cache entries with authenticated Polar API responses. It prints only counts and identifiers, never customer payloads, and is a dry run unless `--repair` is supplied.

```bash
# report drift only
python -m app.reconcile_polar_subscriptions

# explicitly update local lifecycle state and cache
python -m app.reconcile_polar_subscriptions --repair
```

Review `unresolved_user_ids` and `unknown_product_subscription_ids` before repair. Unknown products are never mapped automatically. The command loads product ids from the same `POLAR_PRODUCT_TIER*_ID` settings as the API, so run it with the production environment. Schedule it (e.g. daily dry run with alerting on drift) as the safety net for missed webhooks.

### Chat client surface

| UI | Behavior |
|----|----------|
| Usage indicator | Shows tier + remaining; upgrade opens account |
| Upgrade banner | When over quota |
| Account / pricing | New subscriptions use checkout; active subscriptions use the change-plan API |
| Manage | Portal session whenever a Polar customer is bound, including `past_due` recovery |

---

## Things to keep in mind

1. **Webhooks are the source of truth.** Do not grant paid tiers from the success URL alone.
2. **Sandbox ≠ production.** Separate tokens, secrets, org ids, and product ids. Mixing them causes “paid but free” or API failures.
3. **Product ids must match on every webhook consumer.** Wrong or empty mappings trigger `unknown_product`; the event never grants a tier, but cancellations and revocations still end access.
4. **Webhook target = chat backend.** Same database and Redis; otherwise the UI never sees upgrades.
5. **1:1 customer binding.** One Polar customer cannot be reassigned to another local user (checkout **409**; webhook bind refused).
6. **Idempotency and ordering.** Duplicate `webhook-id` → skip; older `last_event_at` → skip.
7. **Production fail-closed.** With Polar off and `POLAR_SERVER=production` or `ENVIRONMENT=production` (the default), everyone is free. Production refuses to start against the sandbox server.
8. **Secrets.** Never commit real OATs or webhook secrets. Use placeholders in docs and sample env.
9. **Observability.** Webhook and Polar ops attach to wide events (Axiom). Monitor `handler_error`, `unresolved_customer`, `unknown_product`, `stale_event`, webhook latency, quota reservation failures, and stale reservations.
10. **Tier numbers are product decisions.** Change limits/features in `tiers.py`; change prices in Polar (and optionally `price_hint`).

---

## Scenario playbooks

### Local dev without Polar

**Goal:** Full chat features without billing.

```bash
SUBSCRIPTIONS_ENABLED=false
SUBSCRIPTIONS_DEV_DEFAULT_TIER=tier2
```

Checkout/portal return **503**. No webhook needed.

### Sandbox end-to-end smoke test

1. Configure all Polar sandbox env vars and tunnel the webhook.
2. Log into chat as a real user (Clerk uuid preferred so `external_id` is stable).
3. Open Account → Choose a tier → complete Polar sandbox checkout.
4. Confirm Polar dashboard shows a successful delivery to `/api/v1/polar/webhook`.
5. Call `GET /api/v1/subscriptions/me` (or refresh account UI) — tier/status should be paid/`active` (or `trialing`).
6. Send a chat message — tools for that tier should appear.

### Checkout succeeds but tier stays free

Check in order:

| Check | What to look for |
|-------|------------------|
| Webhook deliveries | Polar UI: non-2xx or missing deliveries |
| Signature | **403** → wrong `POLAR_WEBHOOK_SECRET` |
| Product mapping | Env product id must equal payload `product_id` |
| User resolution | Logs with `reason=unresolved_customer` or missing `external_id` |
| Stale event | `subscription_event_skipped` / older `last_event_at` |
| Redis | Key `chat:ent:{user_id}` still old for up to 1h if invalidation failed — delete key |
| Disabled flag | If `SUBSCRIPTIONS_ENABLED=false`, webhooks **202** and do nothing |

### Webhook user not found

The customer’s Polar `external_id` must match `User.uuid` or `str(User.id)`.

- Prefer always creating customers through app checkout (`ensure_customer`) so `external_id` is set correctly.
- If a customer was created manually in Polar without `external_id`, bind will fail until fixed in Polar or recreated via the app.

### Cancel, revoke, past_due, uncancel

| Polar event | Stored status (typical) | Effective tier |
|-------------|-------------------------|----------------|
| `subscription.canceled` | Usually `active` + `cancel_at_period_end=true` | paid until `ends_at` |
| `subscription.revoked` | `revoked` | free immediately |
| `subscription.past_due` | `past_due` | free |
| `subscription.uncanceled` / `active` | `active` | paid tier from product |

Users manage cancel/payment via the **customer portal** (`POST /portal`).

### Unknown event type

Valid signature but type not known to this `polar-sdk` version → **202**, id recorded when present. Safe; upgrade SDK later if you need new events.

### Handler throws

Returns **500** so Polar retries. The `webhook-id` is **not** marked processed until success. Fix the bug / data issue; retries will reapply.

### Subscriptions disabled but Polar still sends webhooks

Endpoint returns **202** immediately. No DB writes. Disable or delete the webhook in Polar for that environment if noise is unwanted.

### Missing product id for a tier

- `GET /tiers` → that tier has `available: false` (when subscriptions enabled).
- `POST /checkout` → **409** `No Polar product configured for …`.

### Portal without a Polar customer

**409** `No Polar customer found for this user`. User must complete (or start) checkout so `ensure_customer` runs, or a customer webhook must bind them.

### Polar customer already linked to another account

Checkout path: **409** `Polar customer is already linked to another account`.
Webhook bind path: refuse silently (no cross-account takeover).

### Over quota

Chat `POST /messages` (or stream) → **402** with:

```json
{
  "reason": "message_limit_reached",
  "usage": { "...": "..." },
  "tier": "free",
  "upgrade_url": "/account"
}
```

UI should send the user to pricing / checkout.

### Manual entitlement cache bust

```bash
# Redis CLI example
DEL chat:ent:<user_id>
```

Or wait for the 1h TTL. Prefer fixing webhooks so `invalidate_entitlement` runs automatically.

### Switching sandbox → production

1. Create production products and webhook in the live Polar org.
2. Deploy **new** env vars (token, secret, org, product ids, `POLAR_SERVER=production`).
3. Do not expect sandbox `polar_customer_id` / subscription ids to work against production API.
4. Existing users re-checkout or re-bind via production flows.

### Stale tier after a webhook you know succeeded

1. Confirm handler outcome `ok` in logs / Axiom.
2. Inspect `user_subscription` row (status, tier, `last_event_at`, product mapping).
3. Delete `chat:ent:{user_id}`.
4. If an older event arrived later with a newer timestamp incorrectly, inspect payload times; ordering is intentional.

---

## API reference

Base: `/api/v1`. All subscription routes below require an **active authenticated user** (Bearer JWT or Clerk session), except the webhook.

### `GET /subscriptions/tiers`

List tier configs for the pricing UI.

**Response:** array of:

| Field | Notes |
|-------|--------|
| `tier` | `free` \| `tier1` \| `tier2` |
| `label` | Display name |
| `monthly_message_limit` | `null` = unlimited |
| `monthly_token_limit` | `null` = unlimited |
| `features` | Sorted feature keys |
| `price_hint` | Cosmetic string |
| `available` | Purchasable: has product id, **or** subscriptions disabled (all shown available in dev) |

### `GET /subscriptions/me`

Current subscription, usage, and active tier config.

**Response:**

```json
{
  "subscription": {
    "tier": "tier1",
    "status": "active",
    "current_period_start": "...",
    "current_period_end": "...",
    "pending_tier": null,
    "pending_effective_at": null,
    "can_manage": true
  },
  "usage": {
    "period_start": "...",
    "period_end": "...",
    "messages_used": 12,
    "messages_limit": 100,
    "tokens_used": 4000,
    "tokens_limit": 200000,
    "remaining": 88
  },
  "tier_config": { "...": "TierOut" }
}
```

When subscriptions are disabled, synthesizes an active sub from the dev default tier and `can_manage=false`.

### `POST /subscriptions/checkout`

```json
{ "tier": "tier1" }
```

| Status | When |
|--------|------|
| **200** | `{ "url", "checkout_id" }` — redirect the browser to `url` |
| **400** | Invalid tier or `free` |
| **409** | No product configured, customer bind conflict, or an existing live subscription must be managed instead |
| **502** | Polar API failure |
| **503** | Subscriptions disabled |

### `POST /subscriptions/change-plan`

```json
{ "tier": "tier2" }
```

An active subscription is updated in place. Upgrades use `invoice`: Polar
collects the prorated difference immediately, and the product changes only if
that payment succeeds. Downgrades use `next_period` and remain visible through
`pending_tier` and `pending_effective_at` until Polar applies them. Webhooks
remain the entitlement source of truth in both cases.

| Status | When |
|--------|------|
| **200** | `{ "requested_tier", "effective", "effective_at" }` |
| **400** | Invalid tier or `free` |
| **409** | No active subscription, same tier, trial/past-due state, or locked subscription |
| **502** | Polar API failure |
| **503** | Subscriptions disabled |

### `POST /subscriptions/portal`

No body.

| Status | When |
|--------|------|
| **200** | `{ "url" }` — customer portal |
| **409** | No `polar_customer_id` on user |
| **502** | Polar API failure |
| **503** | Subscriptions disabled |

### `POST /polar/webhook`

Public endpoint. Polar Standard Webhooks signature headers + body verified with `POLAR_WEBHOOK_SECRET`.

| Status | Meaning |
|--------|---------|
| **202** | Accepted (processed, duplicate, disabled, or deliberately ignored) |
| **400** | Malformed payload |
| **403** | Invalid signature |
| **500** | Handler failed — **retry** |
| **503** | Secret missing or SDK not installed |

---

## Data model (summary)

### `user_subscription` (one row per user)

| Column | Role |
|--------|------|
| `user_id` | Unique FK |
| `polar_customer_id` | Polar customer |
| `polar_subscription_id` | Polar subscription |
| `tier` | Last product-mapped tier string |
| `status` | free / active / trialing / canceled / past_due / … |
| `current_period_start` / `end` | Billing window |
| `canceled_at` | Cancel timestamp when present |
| `pending_tier` / `pending_effective_at` | Scheduled next-period plan change |
| `last_event_at` | Ordering guard |
| `polar_metadata` | Last event metadata (debug) |

### `polar_webhook_event`

| Column | Role |
|--------|------|
| `webhook_id` | Standard Webhooks delivery id (PK) |
| `event_type` | Optional type string |
| `received_at` | Server time |

### `user.polar_customer_id`

Denormalized for checkout/portal and fast webhook resolution. Unique when not null.

---

## Related links

- [Chat API Integration Guide](./CHAT_API.md) — chat turns, streaming, **402** quota
- [API Capabilities Catalog](./API_CAPABILITIES.md) — REST finance API
- [Polar documentation](https://docs.polar.sh) — products, checkout, webhooks, customer portal
