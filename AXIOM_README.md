# Axiom Logging

Wide Events logging system based on [loggingsucks.com](https://loggingsucks.com/) integrated with [Axiom.co](https://axiom.co/).

**One comprehensive event per request** with all context — high-cardinality, high-dimensionality data built throughout the request lifecycle and emitted once at the end.

### Intelligent Tail Sampling

The system automatically keeps:
- All errors (4xx, 5xx status codes)
- All slow requests (above your P99 threshold)
- All VIP users (superusers, enterprise customers)
- All critical endpoints (like WhatsApp webhooks)
- Random sample of successful requests (5% by default)

This keeps costs manageable while never losing important events.

---

## Quick Start

### 1. Sign Up for Axiom

1. Go to [axiom.co](https://axiom.co/) and create a free account
2. Create a new dataset (e.g., `cleverbilling`)
3. Generate an API token: Settings > API Tokens > Create Token with "Ingest" permission
4. Copy the token (starts with `xaat-`)

### 2. Configure Environment Variables

Add to your `.env` file:

```bash
# Required
AXIOM_API_TOKEN=xaat-your-token-here
AXIOM_DATASET=cleverbilling

# Optional
AXIOM_ENABLED=true
AXIOM_SAMPLE_RATE=0.05                  # Sample 5% of successful requests
AXIOM_SLOW_REQUEST_THRESHOLD_MS=2000    # Requests >2s are considered slow
ENVIRONMENT=production
```

### 3. Start Your Application

```bash
docker compose up -d
```

You should see:
```
📊 Initializing Axiom logging to dataset: cleverbilling
✅ Axiom logging initialized successfully
```

### 4. View Logs in Axiom

1. Go to [app.axiom.co](https://app.axiom.co/)
2. Select your dataset
3. Events stream in after 5-10 seconds (batch flush interval)

---

## Architecture

```
Request → WideEventsMiddleware (initialize event)
       ↓
Your Endpoint → enrich_event() (add business context)
       ↓
Response → WideEventsMiddleware (complete event)
       ↓
Tail Sampling Decision → Keep or discard?
       ↓
AxiomClient (async batch) → Axiom API
```

### Key Components

| File | Purpose |
|------|---------|
| `utilities/axiom.py` | Axiom HTTP client with async batching (100 events or 5s flush) |
| `utilities/wide_events.py` | Middleware, tail sampling, `enrich_event()`, `mark_for_logging()` |
| `main.py` | Integration and lifecycle management |

### Modified Files

- `core/config.py` — Axiom settings (`AXIOM_API_TOKEN`, `AXIOM_DATASET`, `AXIOM_ENABLED`, `AXIOM_SAMPLE_RATE`, `AXIOM_SLOW_REQUEST_THRESHOLD_MS`, `DEPLOYMENT_ID`, `REGION`)
- `main.py` — Startup/shutdown handlers, WideEventsMiddleware integration

---

## Configuration

### Tail Sampling Rules

Edit `utilities/wide_events.py` → `_should_sample()`:

```python
def _should_sample(self, event: Dict[str, Any], duration_ms: float) -> bool:
    if event.get("outcome") == "error":
        return True
    if duration_ms > self.slow_request_threshold_ms:
        return True
    # Add custom rules here
    if event.get("user", {}).get("email") == "vip@example.com":
        event["sampling_reason"] = "vip_user"
        return True
    return random.random() < self.sample_rate
```

### Batch Settings

In `main.py` startup:

```python
axiom_client = initialize_axiom(
    dataset=settings.AXIOM_DATASET,
    api_token=settings.AXIOM_API_TOKEN,
    batch_size=100,
    flush_interval=5.0,
    enabled=settings.AXIOM_ENABLED,
)
```

---

## Enriching Events

The middleware captures request/response data automatically. Add business context with `enrich_event()`:

### User + Transaction Context

```python
from app.utilities.wide_events import enrich_event

@router.post("/expenses")
async def create_expense(
    request: Request,
    expense_in: ExpenseCreate,
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.async_get_db),
):
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
            "account_age_days": (datetime.now() - current_user.created).days,
        },
    )

    expense = await crud.expense.create_with_owner(
        db=db, obj_in=expense_in, owner_id=current_user.id
    )

    enrich_event(
        request,
        transaction={
            "type": "expense",
            "id": expense.id,
            "amount_cents": int(expense.amount * 100),
            "category": expense.category.name if expense.category else None,
        },
    )
    return expense
```

### Measuring External Service Duration

```python
start = time.time()
transaction_data = await whatsapp_parser.parse_message(...)
parse_duration = (time.time() - start) * 1000

enrich_event(
    request,
    ai={
        "provider": "openai",
        "model": "gpt-4",
        "parse_duration_ms": round(parse_duration, 2),
        "parsed_type": transaction_data.get("type"),
    },
)
```

### Force Logging (Bypass Sampling)

```python
from app.utilities.wide_events import mark_for_logging

mark_for_logging(request)  # This request will always be logged
```

### Distributed Tracing

```python
from app.utilities.wide_events import get_request_id

request_id = get_request_id(request)
response = await httpx.post(
    "https://other-service.com/api/endpoint",
    headers={"X-Trace-ID": request_id}
)
```

---


## APL Query Reference

### All Errors

```apl
['cleverbilling']
| where outcome == "error"
| project _time, http.path, error.message, user.email
```

### Slow WhatsApp Processing

```apl
['cleverbilling']
| where http.path contains "/whatsapp" and duration_ms > 1000
| project _time, duration_ms, ai.parse_duration_ms, database.duration_ms, user.email
```

### P99 Latency by Endpoint

```apl
['cleverbilling']
| summarize p99 = percentile(duration_ms, 99) by http.path
| where p99 > 500
| sort by p99 desc
```

### VIP User Activity

```apl
['cleverbilling']
| where user.is_superuser == true or user.subscription_tier == "enterprise"
| summarize requests = count(), avg_duration_ms = avg(duration_ms) by user.email
```

### Failed Transactions by User

```apl
['cleverbilling']
| where database.success == false
| summarize failures = count() by user.email, error.message
```

### Request Volume Over Time

```apl
['cleverbilling']
| summarize requests = count() by bin(_time, 1h)
| render timechart
```

### AI Parsing Performance

```apl
['cleverbilling']
| where ai.parse_duration_ms != null
| summarize p99 = percentile(ai.parse_duration_ms, 99), p50 = percentile(ai.parse_duration_ms, 50)
```

### Trace a Single Request

```apl
['cleverbilling']
| where trace_id == "abc-123-def"
| project _time, service, http.path, duration_ms
| sort by _time asc
```

---

## Cost Management

Axiom free tier: 500GB ingested/month, 30 days retention, unlimited queries.

At 5% sampling + always-log-errors, a typical app with 1M requests/day uses ~4GB/month (well within free tier).

### Reduce Costs

```bash
AXIOM_SAMPLE_RATE=0.01                  # 1% instead of 5%
AXIOM_SLOW_REQUEST_THRESHOLD_MS=5000    # Only log requests >5s
```

---

## Troubleshooting

### Checklist

- [ ] `AXIOM_API_TOKEN` is set in `.env` and starts with `xaat-`
- [ ] `AXIOM_DATASET` matches the dataset name in Axiom UI
- [ ] App shows "Axiom logging initialized successfully" on startup
- [ ] Waited 10+ seconds after making requests (batch flush)
- [ ] Correct dataset and time range selected in Axiom UI ("Last 1 hour")
- [ ] Triggered an error request to bypass sampling (errors are always logged)

### Test Token Manually

```bash
curl -X POST https://api.axiom.co/v1/datasets/YOUR_DATASET/ingest \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[{"message": "test", "_time": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}]'
```

Expected: `{"ingested": 1, "failed": 0, ...}`

### Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| 401 Unauthorized | Invalid API token | Regenerate token in Axiom UI |
| 404 Not Found | Dataset doesn't exist | Check dataset name spelling |
| 403 Forbidden | Token lacks permissions | Token needs "Ingest" permission |
| No events after startup message | Sampling | Set `AXIOM_SAMPLE_RATE=1.0` temporarily |
| Events appear 5+ min late | Batch not filling | Lower `batch_size` to 10 or `flush_interval` to 1s |

### Force Immediate Flush (Debugging)

```python
from app.utilities.axiom import get_axiom_client

axiom_client = get_axiom_client()
if axiom_client:
    await axiom_client.flush()
```

---

## Resources

- [Logging Sucks](https://loggingsucks.com/) — Philosophy behind this implementation
- [Axiom Documentation](https://axiom.co/docs)
- [APL Query Language](https://axiom.co/docs/apl/introduction)
- [Axiom Community Discord](https://axiom.co/discord)
