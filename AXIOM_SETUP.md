# Axiom Logging Setup Guide

This implementation follows the **Wide Events** pattern from [loggingsucks.com](https://loggingsucks.com/) for powerful, context-rich logging.

## 🎯 What You Get

### Wide Events Pattern
- **One comprehensive event per request** with ALL context
- High-cardinality, high-dimensionality data for powerful querying
- Built throughout the request lifecycle, emitted once at the end
- No more grep-ing through scattered logs

### Intelligent Tail Sampling
The system automatically keeps:
- ✅ **All errors** (4xx, 5xx status codes)
- ✅ **All slow requests** (above your P99 threshold)
- ✅ **All VIP users** (superusers, enterprise customers)
- ✅ **All critical endpoints** (like WhatsApp webhooks)
- ✅ **Random sample** of successful requests (5% by default)

This keeps costs manageable while never losing important events.

## 🚀 Quick Start

### 1. Sign Up for Axiom

1. Go to [https://axiom.co/](https://axiom.co/) and create a free account
2. Create a new dataset (e.g., `cleverbill`)
3. Generate an API token:
   - Go to Settings → API Tokens
   - Click "Create Token"
   - Give it "Ingest" permission
   - Copy the token (starts with `xaat-`)

### 2. Configure Environment Variables

Add to your `.env` file:

```bash
# Required
AXIOM_API_TOKEN=xaat-your-token-here
AXIOM_DATASET=cleverbill

# Optional
AXIOM_ENABLED=true
AXIOM_SAMPLE_RATE=0.05  # Sample 5% of successful requests
AXIOM_SLOW_REQUEST_THRESHOLD_MS=2000  # Requests >2s are considered slow

# Deployment tracking (auto-detected on Railway)
ENVIRONMENT=production
```

### 3. Start Your Application

The middleware is already integrated! Just start your FastAPI app:

```bash
uvicorn app.main:app --reload
```

You'll see:
```
🚀 Starting application...
📊 Initializing Axiom logging to dataset: cleverbill
✅ Axiom logging initialized successfully
```

### 4. View Your Logs in Axiom

1. Go to [https://app.axiom.co/](https://app.axiom.co/)
2. Select your dataset (`cleverbill`)
3. You'll see your events streaming in with full context!

## 📊 Query Examples

### Find All Failed Checkout Attempts

```apl
['cleverbill']
| where outcome == "error" and http.path contains "/checkout"
| project _time, user.email, error.message, duration_ms
```

### Find Slow WhatsApp Processing

```apl
['cleverbill']
| where http.path contains "/whatsapp" and duration_ms > 1000
| project _time, duration_ms, user.id, whatsapp.message_text
| sort by duration_ms desc
```

### VIP User Activity

```apl
['cleverbill']
| where user.is_superuser == true or user.subscription_tier == "enterprise"
| summarize requests = count(), avg_duration_ms = avg(duration_ms) by user.email
```

### Transaction Errors by Type

```apl
['cleverbill']
| where transaction.type != null and outcome == "error"
| summarize error_count = count() by transaction.type, error.type
```

### P99 Latency by Endpoint

```apl
['cleverbill']
| summarize p99 = percentile(duration_ms, 99) by http.path
| where p99 > 500
| sort by p99 desc
```

## 🔧 Enriching Events in Your Endpoints

The middleware captures request/response data automatically. To add **business context**, use the `enrich_event()` helper:

### Example: Adding User Context

```python
from app.utilities.wide_events import enrich_event

@router.post("/expenses")
async def create_expense(
    request: Request,
    expense_in: ExpenseCreate,
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.async_get_db),
):
    # Add user context to the wide event
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
            "account_age_days": (datetime.now() - current_user.created).days,
        },
    )
    
    # Your business logic...
    expense = await crud.expense.create_with_owner(
        db=db, obj_in=expense_in, owner_id=current_user.id
    )
    
    # Add transaction details
    enrich_event(
        request,
        transaction={
            "type": "expense",
            "id": expense.id,
            "amount_cents": int(expense.amount * 100),
            "category": expense.category.name if expense.category else None,
            "account": expense.account.name if expense.account else None,
        },
    )
    
    return expense
```

### Example: Adding External Service Metrics

```python
from app.utilities.wide_events import enrich_event

@router.post("/whatsapp/webhook")
async def process_webhook(request: Request, ...):
    # Add WhatsApp-specific context
    enrich_event(
        request,
        whatsapp={
            "message_id": message_obj["id"],
            "from_number": sender_number,
            "message_type": "text" if "text" in message_obj else "interactive",
        },
    )
    
    # Measure AI parsing time
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

### Example: Force Logging for Debugging

Sometimes you want to ensure a specific request is logged even if it wouldn't normally be sampled:

```python
from app.utilities.wide_events import mark_for_logging

@router.post("/debug-endpoint")
async def debug_endpoint(request: Request, ...):
    # Force this request to be logged
    mark_for_logging(request)
    
    # Your logic...
```

## 🏗️ Architecture

### Request Flow

```
1. Request arrives → WideEventsMiddleware creates event with:
   - request_id (for tracing)
   - timestamp
   - HTTP details (method, path, headers)
   - Service metadata (version, environment, region)

2. Request processed → Your endpoints enrich with:
   - User context (id, email, subscription)
   - Business context (transaction type, amount, category)
   - External service metrics (AI parse time, DB queries)

3. Response returned → Middleware completes event with:
   - Duration
   - Status code
   - Error details (if any)
   - Performance classification

4. Tail sampling decision → Keep or discard based on:
   - Is it an error? → KEEP
   - Is it slow? → KEEP
   - Is it a VIP user? → KEEP
   - Is it critical endpoint? → KEEP
   - Random sample? → KEEP if in sample rate

5. Event sent to Axiom → Batched for efficiency
   - Buffer of 100 events
   - Auto-flush every 5 seconds
   - Manual flush on shutdown
```

### Key Components

- **`utilities/axiom.py`**: Axiom HTTP client with batching
- **`utilities/wide_events.py`**: Middleware and helper functions
- **`main.py`**: Integration and lifecycle management

## 🎛️ Configuration Options

### Tail Sampling Rules

Edit `utilities/wide_events.py` → `_should_sample()` to customize:

```python
def _should_sample(self, event: Dict[str, Any], duration_ms: float) -> bool:
    # Always keep errors
    if event.get("outcome") == "error":
        return True
    
    # Always keep slow requests
    if duration_ms > self.slow_request_threshold_ms:
        return True
    
    # Add your custom rules here!
    # Example: Always log specific user
    if event.get("user", {}).get("email") == "vip@example.com":
        event["sampling_reason"] = "vip_user"
        return True
    
    # Random sample the rest
    return random.random() < self.sample_rate
```

### Batch Settings

Edit `main.py` startup event:

```python
axiom_client = initialize_axiom(
    dataset=settings.AXIOM_DATASET,
    api_token=settings.AXIOM_API_TOKEN,
    batch_size=100,          # Send to Axiom every 100 events
    flush_interval=5.0,      # Or every 5 seconds
    enabled=settings.AXIOM_ENABLED,
)
```

## 📈 Cost Management

### Free Tier
Axiom's free tier includes:
- 500GB ingested/month
- 30 days retention
- Unlimited queries

At 5% sampling + always-log-errors, a typical app with 1M requests/day will use:
- 50K sampled successful requests → ~5GB/day
- All errors → depends on your error rate
- **Total: ~150GB/month** (well within free tier)

### Tips to Reduce Costs

1. **Lower sample rate**:
   ```bash
   AXIOM_SAMPLE_RATE=0.01  # 1% instead of 5%
   ```

2. **Increase slow request threshold**:
   ```bash
   AXIOM_SLOW_REQUEST_THRESHOLD_MS=5000  # Only log requests >5s
   ```

3. **Add more specific sampling rules** (target what matters)

4. **Use Axiom's retention settings** (keep recent data longer, archive old)

## 🐛 Troubleshooting

### Logs Not Appearing in Axiom

1. **Check API token**:
   ```bash
   echo $AXIOM_API_TOKEN
   # Should start with "xaat-"
   ```

2. **Check dataset name**:
   ```bash
   echo $AXIOM_DATASET
   # Should match your dataset in Axiom UI
   ```

3. **Check logs**:
   ```
   📊 Initializing Axiom logging to dataset: cleverbill
   ✅ Axiom logging initialized successfully
   ```

4. **Test with curl**:
   ```bash
   curl -X POST https://api.axiom.co/v1/datasets/YOUR_DATASET/ingest \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '[{"message": "test", "_time": "2024-12-30T00:00:00Z"}]'
   ```

### Events Being Dropped

- Check sampling: Most successful requests are sampled at 5%
- Errors and slow requests are ALWAYS logged
- Check `sampling_reason` field in logged events

### High Costs

1. Check your actual ingestion in Axiom dashboard
2. Lower sample rate: `AXIOM_SAMPLE_RATE=0.01`
3. Review what's being logged: Are you accidentally enriching events with huge objects?

## 🚀 Advanced: Distributed Tracing

To trace requests across multiple services:

1. **Generate trace ID** (already done by middleware)
2. **Pass to downstream services**:
   ```python
   from app.utilities.wide_events import get_request_id
   
   request_id = get_request_id(request)
   response = await httpx.post(
       "https://other-service.com/api/endpoint",
       headers={"X-Trace-ID": request_id}
   )
   ```

3. **Query across services**:
   ```apl
   ['cleverbill']
   | where trace_id == "abc-123-def"
   | project _time, service, http.path, duration_ms
   | sort by _time asc
   ```

## 📚 Resources

- [Logging Sucks](https://loggingsucks.com/) - Philosophy behind this implementation
- [Axiom Documentation](https://axiom.co/docs)
- [APL Query Language](https://axiom.co/docs/apl/introduction)

## 🤝 Support

Questions or issues? Check:
1. This guide
2. [Axiom Community](https://axiom.co/discord)
3. Your application logs for error messages

