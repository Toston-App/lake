# 🚀 Axiom Logging - Quick Start Checklist

Follow these steps to get your wide events logging up and running in 5 minutes.

## ☑️ Step 1: Create Axiom Account (2 minutes)

1. Go to [https://axiom.co/](https://axiom.co/)
2. Sign up for a free account
3. Click "Create Dataset" 
4. Name it: `cleverbill` (or whatever you prefer)
5. Click "Settings" → "API Tokens"
6. Click "Create Token"
7. Give it "Ingest" permission
8. **Copy the token** (starts with `xaat-`)

## ☑️ Step 2: Configure Environment Variables (1 minute)

Add to your `.env` file:

```bash
# Required - paste your token from step 1
AXIOM_API_TOKEN=xaat-your-token-here

# Optional - use your dataset name from step 1
AXIOM_DATASET=cleverbill

# Optional - defaults shown
AXIOM_ENABLED=true
AXIOM_SAMPLE_RATE=0.05
AXIOM_SLOW_REQUEST_THRESHOLD_MS=2000
ENVIRONMENT=production
```

## ☑️ Step 3: Start Your Application (1 minute)

```bash
cd backend/app
uvicorn app.main:app --reload
```

**Look for these lines in the console:**
```
🚀 Starting application...
📊 Initializing Axiom logging to dataset: cleverbill
✅ Axiom logging initialized successfully
```

✅ If you see these, you're good to go!

❌ If you see `⚠️ Axiom API token not configured`, check your `.env` file.

## ☑️ Step 4: Generate Some Events (1 minute)

Make a few requests to your API:

```bash
# Health check
curl http://localhost:8000/api/v1/utils/health-check/

# WhatsApp webhook (if configured)
# Or any other endpoint in your API
```

**Wait 5-10 seconds** for events to be batched and sent to Axiom.

## ☑️ Step 5: View Your Logs in Axiom (30 seconds)

1. Go to [https://app.axiom.co/](https://app.axiom.co/)
2. Click on your dataset (`cleverbill`)
3. You should see your events streaming in! 🎉

## ☑️ Step 6: Run Your First Query (30 seconds)

Try this query in Axiom:

```apl
['cleverbill']
| where _time > ago(1h)
| project _time, http.method, http.path, duration_ms, outcome
| sort by _time desc
```

You should see all your recent requests with full context!

## 🎯 What You Get Out of the Box

Every request automatically captures:

### HTTP Details
- Method, path, URL, status code
- Query parameters, headers
- User agent, referer

### Performance
- Duration in milliseconds
- Performance classification (fast/normal/slow/very_slow)

### Service Metadata
- Service name, version
- Environment (production/staging/dev)
- Deployment ID (auto-detected on Railway)
- Region

### Network
- Client IP and port

### Errors
- Error type, message
- Stack traces (for exceptions)

### Sampling Info
- Why this event was kept (error/slow/vip/sample)

## 🔍 Try These Queries

### All Errors in Last 24 Hours
```apl
['cleverbill']
| where outcome == "error" and _time > ago(24h)
| project _time, http.path, error.message
```

### Slowest Endpoints
```apl
['cleverbill']
| summarize p99 = percentile(duration_ms, 99) by http.path
| where p99 > 500
| sort by p99 desc
```

### Request Volume by Hour
```apl
['cleverbill']
| summarize requests = count() by bin(_time, 1h)
| render timechart
```

## 🎨 Next Steps: Add Business Context

Now that basic logging works, enrich your events with business context.

### Example: In Your Expense Endpoint

```python
from app.utilities.wide_events import enrich_event

@router.post("/expenses")
async def create_expense(
    request: Request,  # ← Add this
    expense_in: ExpenseCreate,
    current_user: User = Depends(deps.get_current_user),
):
    # Add user context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
        },
    )
    
    # Your existing logic...
    expense = await crud.expense.create(...)
    
    # Add transaction details
    enrich_event(
        request,
        transaction={
            "type": "expense",
            "id": expense.id,
            "amount": float(expense.amount),
        },
    )
    
    return expense
```

Now your events will include `user.email` and `transaction.amount` fields!

Query example:
```apl
['cleverbill']
| where transaction.type == "expense"
| summarize total = sum(transaction.amount) by user.email
```

## 📊 Already Instrumented

The **WhatsApp webhook endpoint** is already fully instrumented with:
- User context
- Message details
- AI parsing metrics and duration
- Database operation metrics
- Cache operation metrics
- Transaction details

Check `backend/app/app/api/api_v1/endpoints/whatsapp.py` to see the implementation.

## 🐛 Common Issues

### "No events in Axiom after 5 minutes"

1. Check your API token:
   ```bash
   echo $AXIOM_API_TOKEN
   # Should start with "xaat-"
   ```

2. Check your dataset name matches:
   ```bash
   echo $AXIOM_DATASET
   ```

3. Test token manually:
   ```bash
   curl -X POST https://api.axiom.co/v1/datasets/YOUR_DATASET/ingest \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '[{"message": "test", "_time": "2024-12-30T00:00:00Z"}]'
   ```

### "I see events but they're missing context"

- Make sure you're calling `enrich_event()` in your endpoints
- Check that you're passing the `request: Request` parameter

### "Too many events / high costs"

Lower the sample rate:
```bash
AXIOM_SAMPLE_RATE=0.01  # 1% instead of 5%
```

Remember: Errors and slow requests are ALWAYS logged, regardless of sample rate.

## 📚 Learn More

- **`IMPLEMENTATION_SUMMARY.md`** - Quick reference of what was built
- **`AXIOM_SETUP.md`** - Detailed setup and configuration guide
- **`WIDE_EVENT_EXAMPLE.md`** - See a real wide event with explanations
- **[loggingsucks.com](https://loggingsucks.com/)** - Philosophy behind this approach

## ✅ Checklist Summary

- [ ] Created Axiom account and dataset
- [ ] Generated API token
- [ ] Added `AXIOM_API_TOKEN` to `.env`
- [ ] Started application
- [ ] Saw initialization message
- [ ] Made test requests
- [ ] Viewed events in Axiom dashboard
- [ ] Ran first query
- [ ] (Optional) Added custom enrichment to endpoints

**Ready? Let's go! 🚀**

