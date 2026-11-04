# Recurring Transaction Detection — Implementation Plan

Status: implemented · Scope: backend only (the frontend lives in another repo)

## 1. Goal

Give users a button that scans their transaction history and suggests recurring
series (subscriptions, rent, salary, regular transfers). The user picks the
suggestions they want and each becomes a recurring template. Every scan skips:

- transactions already in a series,
- series the user already has as a template,
- suggestions the user dismissed.

Decisions already made:

| Topic | Decision |
|---|---|
| Dismissed suggestions | Hidden permanently (new table) |
| Window / thresholds | 13 months (25 for yearly), ≥3 occurrences (≥2 yearly), ±10% amount |
| Types | Expenses, incomes and transfers |
| AI | Yes: merchant normalization + candidate classification, with a deterministic fallback |
| Frontend | Out of scope |
| Access | Free users get **one** scan that finds suggestions; any paid tier (Basic, Premium) scans without limit (§4.5) |

## 2. Architecture overview

```
POST /recurring/detect
        │
        ▼
claim scan access                                           (free: once ever; paid: unlimited)
        │
        ▼
services/recurring_detection.detect()
  1. load      unlinked transactions in the window          (DB)
  2. resolve   description → merchant key                   (alias table → AI → fallback)
  3. skip      merchants/fingerprints already covered       (templates + dismissals)
  4. group     by type + merchant (+ accounts for transfers)
  5. detect    amount clusters → frequency → rule → score   (core, pure)
  6. classify  "is this a recurring commitment?"            (AI, optional)
  7. return    ranked suggestions                           (only the last result is saved, §3.4)

POST /recurring/suggestions/accept   → create_from_transaction() per item
POST /recurring/suggestions/dismiss  → insert fingerprints
DELETE /recurring/suggestions/dismissals/{fingerprint} → undo a dismissal
GET  /recurring/detect/last          → the saved result of the last scan
```

Principle: **date maths is deterministic code; the AI only decides "which texts
are the same merchant" and "is this a commitment or a habit".** If the AI is
disabled, slow or failing, detection still works on normalized text.

## 3. Data model (one migration)

Migration `down_revision = "b7e3d1f9a2c4"` (current head).

### 3.1 `recurring_merchant_alias` (new)

Caches the AI's answer per user so each description is sent to the model once.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| owner_id | int FK user.id, ON DELETE CASCADE | per user: no cross-user sharing of descriptions |
| normalized_description | str | output of `normalize_description()` |
| merchant_key | str | canonical merchant, e.g. `netflix` |
| merchant_name | str | display name, e.g. `Netflix` |
| source | str | `ai` or `rule` |
| created_at | timestamptz | |

Unique `(owner_id, normalized_description)`.

### 3.2 `recurring_suggestion_dismissal` (new)

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| owner_id | int FK user.id, ON DELETE CASCADE | |
| fingerprint | str(32) | see §5.7 |
| label | str nullable | merchant + frequency, so a future "dismissed" list is readable |
| created_at | timestamptz | |

Unique `(owner_id, fingerprint)`.

### 3.3 `recurring_transaction.detection_fingerprint` (new nullable column)

Set when a template is created from a suggestion. It lets later scans skip that
suggestion even after the user renames or edits the template. Unique (partial index) together
with `owner_id`. Not exposed in the public schemas.

### 3.4 `recurring_detection_usage` (new)

One row per user. It records whether the user has used a scan and keeps the
last result, so a free user who reloads the page doesn't lose their only scan.

| Column | Type | Notes |
|---|---|---|
| owner_id | int PK, FK user.id, ON DELETE CASCADE | |
| status | str | `running` / `done` |
| scan_count | int | completed scans that returned suggestions, all tiers |
| claimed_at | timestamptz | when the current or last scan started |
| last_scan_at | timestamptz nullable | when the last scan completed |
| last_result | JSONB nullable | the last `RecurringDetectionResult` |

Models: all three live in `models/recurring_detection.py`, registered in `models/__init__.py`
(and the alembic base import).

## 4. API

All endpoints go in `api/api_v1/endpoints/recurring.py`, using the existing
`_enrich` / `ServiceError` → `raise_http` pattern. Declare them **before** the
`/{id}` routes.

### 4.1 `POST /recurring/detect`

Request body (all optional):

```json
{ "use_ai": true }
```

Response `RecurringDetectionResult`:

```json
{
  "suggestions": [RecurringSuggestion, ...],
  "ai_used": true,
  "scanned_transactions": 1342,
  "scanned_at": "2026-11-03T10:00:00Z",
  "scans_remaining": 0
}
```

`RecurringSuggestion`:

| Field | Notes |
|---|---|
| fingerprint | stable id, used to accept or dismiss |
| type | expense / income / transfer |
| merchant_name | display name |
| description | from the latest occurrence |
| amount | latest amount (the template's amount) |
| amount_min / amount_max | so the UI can show "€9.99–€11.99" |
| account_id, to_account_id, category_id, subcategory_id, place_id | from the latest occurrence |
| frequency, interval, weekdays, monthly_mode, month_days, business_day_adjustment | the detected rule, same names as `RecurrenceRule` |
| occurrences | `[{transaction_id, date, amount}]`, newest first |
| last_date, next_expected_date | |
| confidence | 0–1, combined score |
| kind | `subscription` / `bill` / `salary` / `transfer` / `other` / null when there is no AI |
| reasons | short strings, e.g. `"11 charges on the 15th"`, `"price changed in Mar 2026"` |

Sorted by `confidence` descending, capped at 50.

`scans_remaining` is `0` for a free user after their scan and `null` (unlimited)
for paid users. Access is checked before anything else (§4.5); a free user who
has already scanned gets 402.

Access is checked first (§4.5), so a free user who used their scan gets 402,
not 429. Then the rate limit (every user; free scans that find nothing don't
count, so they can be repeated):
`hit_rate_limit(f"recurring_detect:{user_id}", 3600)`, with
`RECURRING_DETECT_RATE_LIMIT_PER_HOUR` (default 10). Return 429 with
`Retry-After`. Fail open on Redis errors, since one scan costs very little.

### 4.2 `POST /recurring/suggestions/accept`

```json
{ "items": [
    { "fingerprint": "…", "transaction_id": 812, "transaction_type": "expense",
      "frequency": "monthly", "month_days": [15], "amount": 11.99, … }
] }
```

Each item is `RecurringFromTransaction` plus `fingerprint`, so the client sends
back the suggested rule with any edits the user made. `transaction_id` must be
the **latest** occurrence (validated as below).

Response: `[{fingerprint, status: "created" | "error", recurring: RecurringTransactionDetail | null, error: str | null}]`.

Each item runs on its own (commit per item), so one failure doesn't block the rest.

### 4.3 `POST /recurring/suggestions/dismiss`

```json
{ "items": [{ "fingerprint": "…", "label": "Netflix · monthly" }] }
```

`INSERT … ON CONFLICT DO NOTHING`. Returns `{ "dismissed": n }`.

### 4.4 `DELETE /recurring/suggestions/dismissals/{fingerprint}`

Undoes a dismissal. 404 if it isn't there.

### 4.5 Access: one free scan, unlimited for paid tiers

New feature flag in `core/tiers.py`:

```python
# Unlimited recurring detection scans. Every paid tier; free users get one.
FEATURE_RECURRING_DETECTION = "recurring_detection"
```

Added to `ALL_FEATURES` and to tier1's `features`, so both paid tiers have it.
The check uses `get_user_tier()` + `has_feature()`, like investments in
`api/deps.py`. Superusers and `SUBSCRIPTIONS_ENABLED=False` (dev default tier)
follow the same rules as there.

Rules:

- **Paid tier** (has the feature): always allowed; the usage row is updated
  for the saved result and the count.
- **Free tier**: allowed only if the user has no counted scan
  (`scan_count = 0`). Only scans that return at least one suggestion count, so
  a new user with little history doesn't waste it. This includes users who
  scanned while paid and then lapsed: a previous counted scan on any tier uses
  up the free one.
- Only **detect** is limited. Accept, dismiss, undo and `GET /detect/last` stay
  open to everyone, so a free user can act on their one result at any time.

Claiming (in `services/recurring_detection.claim_scan`), so two parallel
requests can't both use the free scan:

1. `INSERT … ON CONFLICT (owner_id) DO UPDATE SET status='running', claimed_at=now()
   WHERE <allowed>` `RETURNING owner_id`. With
   `not_running = status <> 'running' OR claimed_at < now() - interval '10 minutes'`
   (so a row left `running` by a crashed process doesn't block forever),
   `<allowed>` is `not_running` for paid users and `scan_count = 0 AND not_running`
   for free users. A user with no row yet always gets the insert.
2. No row returned → free user with `scan_count > 0`: 402; otherwise 409
   "a scan is already running" (two parallel first scans: one runs, one gets 409).
3. On success: `status='done'`, `last_scan_at`, `last_result`, and
   `scan_count += 1` when the scan returned suggestions; commit.
4. On any error during the scan: reset `status='done'` without touching
   `scan_count`, so a failed scan doesn't use the free one. AI fallback is not an
   error: a scan that completes without AI counts.

402 body, same shape as the chat quota:

```json
{ "reason": "recurring_detection_limit",
  "tier": "free",
  "last_scan_at": "2026-10-01T09:12:00Z",
  "upgrade_url": "/account" }
```

### 4.6 `GET /recurring/detect/last`

Returns the saved `last_result` with `scans_remaining` recomputed for the
current tier, or 404 if the user never scanned. Before returning, drop
suggestions whose fingerprint is now in a template or dismissal (§5.8 step 2),
so accepted and dismissed items don't show up again. Not rate limited.

`GET /users/me` gains `recurring_detection: {"scans_remaining": 0 | 1 | null}` so
the frontend can show the button, the "1 free scan" label or the upgrade prompt
without calling detect.

## 5. Detection logic

### 5.1 Load (service)

One query per type: only the columns needed, `owner_id = user.id`,
`recurring_id IS NULL`, `date >= today - 25 months`. Join `place.name` for
expenses and incomes. Map into a plain dataclass:

```python
@dataclass(frozen=True)
class TxPoint:
    id: int
    type: RecurringType
    date: date
    amount: float
    description: str | None
    place_name: str | None
    account_id: int | None
    to_account_id: int | None     # transfers
    category_id: int | None
    subcategory_id: int | None
    place_id: int | None
```

The 25-month window is only used for yearly detection; other frequencies only
look at the last 13 months (filtered in core).

"Today" is `local_today("UTC")` for now (users have no timezone column); pass it
in so tests can fix it.

### 5.2 Normalize (core, deterministic)

`normalize_description(text) -> str`:

1. Lowercase and remove accents (`unicodedata.normalize("NFKD")`).
2. Remove URLs' scheme/`www.`, and the TLD on domain-like tokens (`netflix.com` → `netflix`).
3. Remove dates, card fragments (`*1234`, `xxxx1234`), long digit runs (≥3) and reference-like tokens (mixed letters and digits ≥6 characters).
4. Remove a small stop list of payment noise: `pago`, `payment`, `compra`, `pos`, `card`, `tarjeta`, `debit`, `cargo`, `transferencia`, `recibo`, `ref` … (Spanish + English, kept in a constant).
5. Replace punctuation with spaces, collapse whitespace, trim.

If the result is empty, use the normalized `place_name`. If that is empty too,
the transaction has no text key (see §5.4).

### 5.3 Resolve merchants (alias table → AI → fallback)

`services/recurring_detection._resolve_merchants(db, user, points)`:

1. Collect the distinct normalized descriptions.
2. Load the existing aliases for them from `recurring_merchant_alias`.
3. Send the missing ones to the AI (see §6.1) in chunks of 150, at most 4 chunks per scan (anything beyond waits for the next scan and uses the fallback this time).
4. Save the AI answers with `source="ai"`.
5. Fallback for anything unresolved (AI disabled, timeout, error): `merchant_key = normalized_description`, `merchant_name = title-cased original`. **Don't save the fallback**, so the AI gets another try next scan.

### 5.4 Group

Group key (points are keyed by `(type, id)` throughout: ids are per table,
so an expense and an income can share one):

- expense / income: `(type, merchant_key)`, ignoring the account, so a
  subscription moved to another card stays one series. The suggestion uses the
  latest occurrence's account.
- transfer: `(type, account_id, to_account_id, merchant_key or None)`, since
  for transfers the accounts are the identity.
- No text key (expense/income without description or place):
  `(type, account_id, category_id, subcategory_id)`, and the amount tolerance
  in §5.5 is tightened to exact matches (±0.01) to avoid grouping unrelated
  spending.

### 5.5 Split by amount

Within a group, sorted by date:

1. Greedy chronological clustering: a transaction joins the cluster whose
   rolling median (last 3) is within ±10%; otherwise it starts a new cluster.
2. Merge pass for price changes: two clusters merge when they don't overlap in
   time (`a.last_date < b.first_date`), the gap between them fits the same
   frequency, and the merged series passes §5.6. Record a reason like
   `"price changed in Mar 2026"`.
3. Same-day duplicates (same date and amount): keep one, so a duplicate import
   doesn't break the gaps.

### 5.6 Detect frequency and infer rule

For each cluster with enough points, compute the gaps in days between
consecutive dates and test each frequency:

| Frequency | Gap accepted | Extra check | Stored as |
|---|---|---|---|
| weekly | 6–8 | same weekday in ≥75% | `weekly`, `interval=1`, `weekdays=[mode]` |
| every 2 weeks | 12–16 | same weekday in ≥75% | `weekly`, `interval=2` |
| semimonthly | 10–20, alternating | dates form two day-of-month clusters (e.g. 1 & 15, 15 & last) | `semimonthly`, `month_days=[a, b]` |
| monthly | 26–35 | day of month within ±3 of the mode (after weekend correction) | `monthly`, `day_of_month`, `month_days=[mode]`; use `-1` only when ≥75% of the dates are their month's last day (or the Friday before a weekend month end) and the months differ in length; rent on the 28th or 30th keeps its day |
| yearly | 350–380 | same month, day ±5 | `yearly` |

Daily is not detected.

Scoring each candidate frequency:

- `fit = matching gaps / all gaps`. A gap of about 2× the period counts as half a match (one missed or unlogged period).
- Accept if `fit ≥ 0.75` and the cluster has ≥3 points (≥2 for yearly, which needs the 25-month window).
- If several fit, pick the highest `fit`, then the lowest gap standard deviation.

Business-day adjustment: for monthly and semimonthly, look at months where the
nominal day falls on a weekend. If the actual dates are consistently the Friday
before, use `previous`; the Monday after, use `next`; otherwise `none`. Weekend
nominal dates are excluded from the ±3 day check above so the shifts don't count
as noise.

Recency: drop the candidate if `last_date + 1.5 × period < today` (the series
has probably stopped). `next_expected_date` is what the generator will create
after accept: the next occurrence on or after today. Dates the series was due on
between its last charge and today are listed as reasons
(`"nothing recorded on 2026-10-24"`) and are **not** back-filled on accept (§7).

### 5.7 Fingerprint

`sha256("|".join([type, merchant_key, str(account_id or ""), str(to_account_id or ""), frequency, str(interval)]))[:32]`

- Accounts are included only for transfers (empty for expense/income, matching §5.4).
- The amount is excluded, so a price change keeps the same fingerprint.
- The fingerprint is stable because `merchant_key` comes from the cached alias.

### 5.8 Skip what the user already has

Applied before grouping (step 1) and after detection (steps 2–3):

1. Transactions with `recurring_id` set are never loaded (generated copies and
   the source transaction).
2. Any candidate whose fingerprint is in `recurring_transaction.detection_fingerprint`
   or `recurring_suggestion_dismissal` for this user is dropped.
3. Any candidate matching an existing template (active or paused, including
   manually created ones) is dropped. Match: same type, the template's
   description resolves to the same `merchant_key` (via the same alias lookup),
   and for transfers the same accounts. If the template has no description,
   fall back to same type + account + amount within ±10%. Each template covers
   at most **one** series, the closest in amount and only within a factor of 2,
   so a template for a merchant's 2.99 plan doesn't hide its 29.99 plan.
   Templates created from a suggestion are left out of this step; their
   fingerprint (step 2) already hides their series. Applied before step 2.

### 5.9 Deterministic confidence

```
base = 0.45·fit + 0.25·amount_stability + 0.20·min(points/6, 1) + 0.10·recency
```

- `amount_stability = 1 − min(stdev/median, 1)`
- `recency = 1` if the last date is within 1 period, falling linearly to 0 at 1.5 periods

## 6. AI step

New module `ai/recurring_detection.py`. It uses `pydantic_ai.Agent` with
`build_model()` from `ai/agent.py` (OpenRouter, the same provider as the chat).
Both agents use structured `output_type` Pydantic models, temperature 0,
a timeout of `RECURRING_DETECTION_AI_TIMEOUT_SECONDS`, and `retries=1`.

Privacy: only descriptions, place names, transaction type and category names are
sent — never amounts tied to identities, account names, ids, emails or the
user's name. Document this in the endpoint docstring.

### 6.1 Merchant resolution

Input: `[{i, type, text, place}]` (normalized description + place name).
Output:

```python
class MerchantItem(BaseModel):
    i: int
    merchant_key: str   # lowercase ascii slug, e.g. "netflix", "landlord_rent"
    merchant_name: str  # "Netflix"

class MerchantBatch(BaseModel):
    items: list[MerchantItem]
```

Instructions (summary):

- Same real-world counterparty → same `merchant_key`.
- Different products from the same company stay separate only if clearly different services (e.g. `amazon` vs `amazon_prime`).
- Generic texts ("rent", "alquiler") get a generic key ("rent").
- Unclear → return the input text as the slug; never invent a merchant.

Validation: drop items with an unknown `i`; slugify `merchant_key` server-side;
anything missing uses the fallback.

### 6.2 Candidate classification

Runs once per scan on the candidates left after §5.8, at most 50.

Input per candidate: `{i, type, merchant_name, category_name, frequency, interval, occurrences, amount_min, amount_max}`.

Output:

```python
class CandidateLabel(BaseModel):
    i: int
    kind: Literal["subscription", "bill", "salary", "transfer", "habit", "other"]
    is_commitment: bool
    confidence: float = Field(ge=0, le=1)
```

How it's used:

- `final = base × (0.6 + 0.4·confidence)` if `is_commitment`, otherwise `base × (1 − 0.7·confidence)`.
- Drop candidates with `final < 0.35` (setting `RECURRING_DETECTION_MIN_CONFIDENCE`).
- `kind = "habit"` is not shown to the user; it's mapped to `other` in the response.
- Without AI, `final = base` and `kind = null`.

### 6.3 Settings (`core/config.py`)

```python
RECURRING_DETECTION_AI_ENABLED: bool = True
RECURRING_DETECTION_MODEL: str | None = None      # falls back to OPENROUTER_MODEL
RECURRING_DETECTION_AI_TIMEOUT_SECONDS: int = 20
RECURRING_DETECTION_MIN_CONFIDENCE: float = 0.35
RECURRING_DETECT_RATE_LIMIT_PER_HOUR: int = 10
```

`use_ai=false` in the request, or the setting off, skips both AI calls.

### 6.4 Cost and observability

- Merchant calls only for descriptions not seen before, so most later scans make one small classification call.
- Log model, token counts and `usage.cost` (OpenRouter returns it with `openrouter_usage.include`) through the request's `_enrich` payload: `{type: "recurring_detect", scanned, candidates, suggestions, ai_used, ai_cost}`.
- Not counted against the chat token quota. Free users' cost is bounded by the single scan (§4.5).

## 7. Accept flow (service)

`accept_suggestions(db, user, items)`, for each item:

1. Load the transaction with the existing getter. It must belong to the user,
   have `recurring_id IS NULL`, and be the newest transaction of its series.
   The series is re-detected once per accept request (cached aliases, no AI),
   so it spans price changes but not the merchant's other plans. A transaction
   outside any detected series falls back to: no newer unlinked transaction
   with the same resolved merchant and type within ±10%. This stops the client
   from passing an old transaction, which would back-fill copies.
2. Call `recurring_service.create_from_transaction(db, user, item)`.
3. Set `template.detection_fingerprint = item.fingerprint` and commit.
   `create_recurring` takes a new keyword `detection_fingerprint=None` so this
   happens in the same commit. A fingerprint that already has a template is an
   error for that item; a partial **unique** index on
   `(owner_id, detection_fingerprint)` makes that hold for concurrent accepts.
   Dates the series missed before today are skipped, not back-filled.
4. The source transaction counts as the rule's nearest date (within ±3 days),
   so a charge moved to Friday the 13th for a series due on the 15th doesn't
   create another one two days later (`snap_source_to_rule`).
5. Catch `ServiceError` per item and report it. Other exceptions roll back that
   item and report a generic error.

Older matching transactions are **not** linked to the template. Linking them
would make `delete_recurring(delete_generated=True)` delete the user's real
history. The fingerprint and template match in §5.8 already stop them coming back.

## 8. Files

| File | Change |
|---|---|
| `alembic/versions/<rev>_recurring_detection.py` | 3 tables + `detection_fingerprint` column |
| `app/models/recurring_detection.py` | new: alias, dismissal and usage tables |
| `app/models/recurring_transaction.py` | `detection_fingerprint` column |
| `app/models/__init__.py` | register models |
| `app/core/tiers.py` | `FEATURE_RECURRING_DETECTION` on both paid tiers |
| `app/api/api_v1/endpoints/users.py` | `recurring_detection.scans_remaining` in `/users/me` |
| `app/core/recurrence_detection.py` | new, pure: normalize, cluster, detect, score, fingerprint |
| `app/ai/recurring_detection.py` | new: two agents + call helpers with fallback |
| `app/services/recurring_detection.py` | new: load, resolve, skip, detect, classify, accept, dismiss |
| `app/services/recurring.py` | `create_recurring(..., detection_fingerprint=None, snap_source_to_rule=False)` |
| `app/schemas/recurring.py` | suggestion, result, accept, dismiss schemas |
| `app/api/api_v1/endpoints/recurring.py` | 5 endpoints, before `/{id}` |
| `app/core/config.py` | settings in §6.3 |

## 9. Tests

AI calls are replaced with `pydantic_ai.models.function.FunctionModel` via
`agent.override(model=...)`, so tests never hit the network.

**Core (`tests/core/test_recurrence_detection.py`)**, no DB:

- normalize: card fragments, dates, domains, accents, stop words, empty → place name
- monthly on the 15th, with weekend dates moved to Friday → `previous`
- monthly at month end → `month_days=[-1]`
- weekly, every 2 weeks, semimonthly (1 & 15), yearly with 2 points over 25 months
- price change partway through → one series, reason recorded
- one missed month still detected, with lower confidence
- stopped subscription (last date > 1.5 periods ago) → dropped
- irregular spending at the same merchant → not detected
- two subscriptions from the same merchant at different amounts → two series (their fingerprints get the rounded amount added so they differ)
- same-day duplicates don't break the gaps
- fingerprint is stable across price changes and differs by frequency

**Service (`tests/services/test_recurring_detection.py`)**, real DB:

- transactions with `recurring_id` are ignored
- a candidate matching an existing (paused) template is skipped
- a dismissed fingerprint is skipped; undoing the dismissal brings it back
- accept: creates a template from the latest transaction, sets `detection_fingerprint`, generates no back-dated copies
- accept with an older transaction of the group → error for that item only
- second scan after accept doesn't return the accepted suggestion, even after the template is renamed
- AI merchant resolution groups `"NETFLIX.COM 8473"` and `"netflix subscription"`; aliases are cached and the second scan makes no merchant call
- AI timeout/error → falls back, `ai_used=false`, the fallback isn't cached
- classifier `habit` with high confidence → suggestion dropped

**API (`tests/api/test_recurring_detection.py`)**:

- auth; another user's transaction in accept → per-item error
- partial failure in a batch accept
- rate limit → 429
- dismiss is idempotent; DELETE of an unknown fingerprint → 404
- free user: first scan 200 with `scans_remaining=0`, second scan 402
- free user: a scan with no suggestions doesn't count
- a claim held by a running scan → 409 until it goes stale (10 min)
- free user: scan that raises → free scan not used, next scan 200
- free user after a scan: accept, dismiss and `GET /detect/last` still work
- tier1 and tier2: repeated scans allowed, `scans_remaining=null`; rate limit still applies
- lapsed paid user who scanned before → 402
- `GET /detect/last` hides accepted and dismissed suggestions; 404 before any scan
- `/users/me` returns `scans_remaining` 1 → 0 for free, null for paid

## 10. Implementation order

1. Migration + models (§3).
2. `core/recurrence_detection.py` with its tests: the bulk of the logic, no AI.
3. Service + endpoints with AI disabled; service and API tests.
4. AI module, wired in with the fallback; AI tests with `FunctionModel`.
5. Settings, tier access (§4.5), `GET /detect/last`, rate limit, logging. Run the full suite and ruff.
6. Try it on a seeded or demo user (`demo_data.py`) and tune the thresholds.

## 11. Risks and open points

- **Thresholds need tuning on real data.** All of them are constants or settings so they can be adjusted after step 6.
- **Merchant mistakes by the AI** (merging two different merchants) would hide or merge suggestions. This is limited because the amount split and the date check still have to pass. A later endpoint could let users correct an alias.
- **Data sent to OpenRouter**: descriptions can contain personal text. Covered by sending only description/place/category text, and by the `use_ai` switch. Consider mentioning it in the privacy policy.
- **Users have no timezone**: "today" is UTC. Off by one day at most near midnight; it only affects the recency check.
- **Large histories**: in-memory processing is fine up to tens of thousands of transactions per user. If needed later, the load step can aggregate in SQL first.
