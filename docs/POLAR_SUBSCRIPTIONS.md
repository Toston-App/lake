# Polar Subscriptions

Operator guide for the [Polar.sh](https://polar.sh) billing integration that powers Toston Chat tiers, usage quotas, and feature gates.

Polar is the **billing provider**. Lake owns **entitlements**, **usage counting**, and **tool/feature gating**. A successful checkout alone does not upgrade a user — Polar **webhooks** are the source of truth for paid state.

For chat API behavior (including HTTP **402** when over quota), see [CHAT_API.md](./CHAT_API.md).

**Reference client:** [`chat/`](../chat/) — `chat/src/api/subscriptions.ts`, `chat/src/hooks/useSubscription.ts`.

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

When subscriptions are **disabled**, every user gets `SUBSCRIPTIONS_DEV_DEFAULT_TIER` (default `tier3` for local full-chat). When `POLAR_SERVER=production`, that default is forced to **`free`** so a misconfigured production deploy never grants a paid tier without Polar.

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
| `POLAR_PRODUCT_TIER3_ID` | for checkout | — | Polar product id for tier 3 |
| `SUBSCRIPTIONS_DEV_DEFAULT_TIER` | no | `tier3` | Tier when subscriptions are disabled. Forced to `free` if `POLAR_SERVER=production` |

Product ids are optional for app boot, but **required to create checkouts**. They are also the mapping used when webhooks arrive: unknown product id → effective tier `free`.

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
2. **Create three recurring products** — one per paying tier (`tier1`, `tier2`, `tier3`). Prices are cosmetic in our UI (`price_hint` in code); Polar is the system of record for charging.
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
   | `subscription.canceled` | Cancel path |
   | `subscription.uncanceled` | Restore after cancel |
   | `subscription.revoked` | Hard revoke → treated like canceled |
   | `subscription.past_due` | Payment failure state |
   | `customer.created` | Bind `polar_customer_id` via `external_id` |
   | `customer.updated` | Same bind path |

   Other event types (checkout, order, benefit grant, …) are **acknowledged and ignored** so Polar does not retry forever.

7. **Copy the webhook secret** → set `POLAR_WEBHOOK_SECRET`.
8. **Paste product ids** into `POLAR_PRODUCT_TIER1_ID` / `TIER2` / `TIER3`.
9. Set `POLAR_SERVER` to `sandbox` or `production` to match the org you used.
10. Set `POLAR_SUCCESS_URL_BASE` to the **public origin users hit for the chat app** if it differs from `SERVER_HOST` (checkout returns to `{base}/account?checkout=success`).

### 3. Database migrations

Subscription tables ship as Alembic revisions:

| Revision | What it adds |
|----------|----------------|
| `a1b3c5d7e9f0` | `user_subscription`, `user.polar_customer_id` |
| `b2c4d6e8f0a1` | Unique customer binding, `last_event_at`, `polar_webhook_event` idempotency ledger |

After pull:

```console
$ docker-compose exec backend bash
$ alembic upgrade head
```

### 4. Environment checklists

#### Local development (no billing)

```bash
SUBSCRIPTIONS_ENABLED=false
SUBSCRIPTIONS_DEV_DEFAULT_TIER=tier3   # full features locally
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
POLAR_PRODUCT_TIER3_ID=...
POLAR_SUCCESS_URL_BASE=https://<your-tunnel-or-staging-host>
```

Webhook URL must hit **this** backend instance (same Postgres + Redis as chat).

#### Production

```bash
SUBSCRIPTIONS_ENABLED=true
POLAR_SERVER=production
# production OAT, webhook secret, org id, product ids only
# never reuse sandbox tokens or product ids
```

If Polar is off in production, everyone is **free** (`SUBSCRIPTIONS_DEV_DEFAULT_TIER` is forced to `free`).

---

## How it works

### Tier catalog

Defined in `backend/app/app/core/tiers.py` (numbers are tunable starting defaults):

| Tier | Message limit / mo | Token limit / mo | Features (examples) |
|------|--------------------|------------------|---------------------|
| `free` | 100 | 900_000 | none |
| `tier1` | 100 | 200_000 | `search_web` |
| `tier2` | 500 | 1_000_000 | + `charts`, `advanced_analytics` |
| `tier3` | unlimited | unlimited | all (`bulk_operations`, `priority_support`, …) |

- `polar_product_id` is injected from env via `configure_product_ids`.
- Checkout uses product id → Polar.
- Webhooks reverse-map product id → tier via `tier_for_product`. **Unknown product id → `free`.**

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

`GET /me` sets `can_manage=true` only when the user has a bound Polar customer **and** a subscription row with status `active` or `trialing`.

### Webhook pipeline

```text
POST /api/v1/polar/webhook  (no auth header; signature only)
  │
  ├─ SUBSCRIPTIONS_ENABLED=false → 202 (silent ack, no retries)
  ├─ webhook-id already in polar_webhook_event → 202
  ├─ missing secret / no SDK → 503
  ├─ invalid signature → 403
  ├─ unknown event type (valid signature) → mark id, 202
  ├─ handler error → 500 (Polar retries; do NOT mark id processed)
  └─ success → mark webhook-id processed, 202
```

#### User resolution on subscription events

1. `User.polar_customer_id == payload.customer_id` (fast path)
2. Else `UserSubscription.polar_customer_id`
3. Else Polar API `customers.get` → `external_id` → local user by uuid or numeric id; then bind

If no local user is found, the event is logged (`no_local_user`) and skipped (still 202 after successful handler path that returns early — no state change).

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
```

##### Layer 1: delivery idempotence (`webhook-id`)

| Piece | Detail |
|-------|--------|
| Header | Standard Webhooks `webhook-id` (unique per Polar delivery attempt identity) |
| Table | `polar_webhook_event` — PK = `webhook_id` |
| Check | At the start of `POST /polar/webhook`: if id already stored → **202** `outcome=duplicate`, handler not run |
| Record | **Only after** successful handling (or safe unknown-type ack). Failed handlers return **500** and **do not** insert the id so Polar can retry |
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

1. Derive `event_at` from the payload (`modified_at` / `updated_at` / `created_at`) or the `webhook-timestamp` header, else “now”.
2. If the user already has `last_event_at` and `event_at < last_event_at` → **return without writing** (stale).
3. Otherwise `INSERT … ON CONFLICT (user_id) DO UPDATE` the full subscription snapshot and set `last_event_at = event_at`.

So:

| Situation | Result |
|-----------|--------|
| Same logical sub state applied twice with **different** delivery ids but same/newer timestamps | Second write overwrites with equivalent fields (harmless) |
| Older event arrives after a newer one was applied | Skipped (`subscription_event_skipped` / `stale_event`) |
| Retry of a failed handler that **already committed** the upsert but **died before** marking `webhook-id` | Layer 1 does not short-circuit; layer 2 re-applies the same or newer snapshot (upsert is safe) |
| Two different events with the **same** `event_at` | Second is **not** rejected by the `<` check (only strictly older is skipped); last write wins |

##### Other “safe to retry” paths

| Path | Behavior |
|------|----------|
| `ensure_customer` | Lookup by `external_id` first; create only if missing; bind is refused if customer already owned by another user |
| `set_polar_customer_id` | No-op-ish when same user; **ValueError** if another user owns the id |
| `ensure_free_row` | `ON CONFLICT DO NOTHING` on `user_id` |
| Entitlement cache | Webhook success **invalidates** Redis; replaying a successful path may delete the cache again (fine — next read reloads DB) |
| Checkout / portal create | **Not** fully idempotent: each call can create a **new** Polar checkout/session URL. Safe to call again, but you get a new session, not the same id |

##### What is *not* guaranteed

- **Exactly-once side effects outside this DB** — we only control local rows + Redis invalidation. Polar’s own charges are Polar’s domain.
- **Strict equality of concurrent handlers** for the *same* new `webhook-id`: two workers could both pass the “not processed” check before either inserts the ledger. In practice you run one backend writer path; the PK + ordering still keep the subscription row consistent. If you scale webhook receivers horizontally, prefer a single consumer or rely on the unique `webhook_id` insert race (one wins; both may have run the handler once — still safe if upsert is pure).
- **Checkout button double-click** — may open two checkout sessions; only one subscription should result on Polar’s side; we follow whatever webhooks arrive.

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
| `active`, `trialing` | Row’s `tier` (from product mapping) |
| anything else (`free`, `canceled`, `past_due`, `incomplete`, …) | **`free`** |

The row may keep historical `tier` for display/debug; entitlements only grant paid features for active/trialing.

### Entitlements, usage, and enforcement

Resolution (`services/entitlements.py`):

1. If subscriptions disabled → dev default tier.
2. Else Redis `chat:ent:{user_id}` (TTL **1 hour**).
3. On miss → load `user_subscription`, compute effective tier, cache.

Usage window:

- Paying active sub with `current_period_start` → that timestamp to `current_period_end`.
- Free / inactive → first day of current UTC calendar month.

Usage sources: aggregate `chat_obs_turns` for the user since period start, **excluding** `status = error` turns (errors do not burn quota).

Limits from the tier config:

- `messages_used >= monthly_message_limit` → deny (`message_limit_reached`)
- `tokens_used >= monthly_token_limit` → deny (`token_limit_reached`)

Chat endpoints call `check_quota` and return **HTTP 402** with usage payload when denied. Successful subscription webhooks call `invalidate_entitlement` so the next turn sees the new tier without waiting for TTL.

### Chat client surface

| UI | Behavior |
|----|----------|
| Usage indicator | Shows tier + remaining; upgrade opens account |
| Upgrade banner | When over quota |
| Account / pricing | Lists tiers from `GET /tiers`; Choose → checkout redirect |
| Manage | Portal session when `can_manage` |

---

## Things to keep in mind

1. **Webhooks are the source of truth.** Do not grant paid tiers from the success URL alone.
2. **Sandbox ≠ production.** Separate tokens, secrets, org ids, and product ids. Mixing them causes “paid but free” or API failures.
3. **Product ids must match on every webhook consumer.** A deploy with wrong or empty product ids maps paid products to `free`.
4. **Webhook target = chat backend.** Same database and Redis; otherwise the UI never sees upgrades.
5. **1:1 customer binding.** One Polar customer cannot be reassigned to another local user (checkout **409**; webhook bind refused).
6. **Idempotency and ordering.** Duplicate `webhook-id` → skip; older `last_event_at` → skip.
7. **Production fail-closed.** With Polar off and `POLAR_SERVER=production`, everyone is free.
8. **Secrets.** Never commit real OATs or webhook secrets. Use placeholders in docs and sample env.
9. **Observability.** Webhook and Polar ops attach to wide events (Axiom). Look for `webhook.outcome`, `polar.op`, `reason=no_local_user`, `stale_event`, etc.
10. **Tier numbers are product decisions.** Change limits/features in `tiers.py`; change prices in Polar (and optionally `price_hint`).

---

## Scenario playbooks

### Local dev without Polar

**Goal:** Full chat features without billing.

```bash
SUBSCRIPTIONS_ENABLED=false
SUBSCRIPTIONS_DEV_DEFAULT_TIER=tier3
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
| User resolution | Logs with `reason=no_local_user` or missing `external_id` |
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
| `subscription.canceled` | `canceled` (or payload status) | free |
| `subscription.revoked` | `canceled` if status missing | free |
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
| `tier` | `free` \| `tier1` \| `tier2` \| `tier3` |
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
| **409** | No product configured for tier; or customer bind conflict |
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
