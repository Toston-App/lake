# Wide Event Example

This shows what a real **Wide Event** looks like in Axiom after processing a WhatsApp message to create an expense.

## Traditional Logging (What You Had Before)

Multiple scattered log lines across different files:

```
2024-12-30 14:23:15 - whatsapp_requests - INFO - Text message received from +52xxx: Gasté 250 pesos en restaurante
2024-12-30 14:23:15 - main - INFO - Request: POST /api/v1/whatsapp/webhook from cleverbill.ing
2024-12-30 14:23:16 - whatsapp_requests - INFO - Parsed transaction: expense
2024-12-30 14:23:16 - main - INFO - Response status: 200
```

**Problem**: No context! Good luck finding:
- Which user sent this?
- How long did it take?
- Did the AI parse it correctly?
- Was it slow? Did it error?
- Which account was used?

You'd have to grep through multiple logs, correlate timestamps, and hope you find everything.

## Wide Event (What You Have Now)

One comprehensive event with ALL context:

```json
{
  "_time": "2024-12-30T14:23:15.234Z",
  "request_id": "abc-123-def-456",
  "trace_id": "abc-123-def-456",
  
  "service": "CleverBilling API",
  "version": "0.9.0",
  "environment": "production",
  "deployment_id": "deploy-789",
  "region": "us-east-1",
  
  "http": {
    "method": "POST",
    "path": "/api/v1/whatsapp/webhook",
    "url": "https://api.cleverbill.ing/api/v1/whatsapp/webhook",
    "scheme": "https",
    "query_params": {},
    "user_agent": "WhatsApp/2.23.20",
    "host": "api.cleverbill.ing",
    "status_code": 200,
    "content_type": "application/json"
  },
  
  "network": {
    "client_ip": "157.240.22.35",
    "client_port": 51234
  },
  
  "outcome": "success",
  "duration_ms": 847.23,
  "performance": "slow",
  
  "webhook": {
    "type": "whatsapp",
    "entries_count": 1
  },
  
  "whatsapp": {
    "message_id": "wamid.HBgNNTIxMT...",
    "from_number_hash": "sha256:abc123...",
    "message_type": "text",
    "has_user": true
  },
  
  "user": {
    "id": 42,
    "email": "user@example.com",
    "is_superuser": false,
    "has_default_account": true
  },
  
  "ai": {
    "provider": "openai",
    "parse_duration_ms": 523.12,
    "message_length": 38,
    "context_items": {
      "categories": 15,
      "places": 23,
      "accounts": 3
    }
  },
  
  "parsing": {
    "success": true,
    "transaction_type": "expense",
    "amount": 250.0,
    "has_category": true,
    "has_place": true,
    "has_account": true
  },
  
  "cache": {
    "operation": "store_transaction",
    "success": true,
    "duration_ms": 12.45
  },
  
  "database": {
    "operation": "create_expense",
    "duration_ms": 234.56,
    "success": true
  },
  
  "transaction": {
    "type": "expense",
    "id": 1234,
    "amount": 250.0,
    "source": "whatsapp"
  },
  
  "sampling_reason": "critical_endpoint",
  "force_log": true
}
```

## The Power of Wide Events

### One Query, Complete Answer

**Question**: "Why was that WhatsApp request slow?"

**Traditional approach** (grep-ing):
```bash
grep "whatsapp" app.log | grep "250 pesos"
# Find timestamp
grep "14:23:15" app.log
# Manually piece together info from multiple lines
# Still don't know WHY it was slow
```

**Wide Events approach** (Axiom query):
```apl
['cleverbill']
| where http.path contains "/whatsapp" and duration_ms > 500
| project 
    _time,
    duration_ms,
    ai.parse_duration_ms,
    database.duration_ms,
    cache.duration_ms,
    user.email
```

**Answer**: AI parsing took 523ms (main culprit)

### Complex Business Queries

**Question**: "Show me all failed expense creations from WhatsApp in the last 7 days"

```apl
['cleverbill']
| where transaction.source == "whatsapp"
| where transaction.type == "expense"
| where database.success == false
| summarize 
    failures = count(),
    avg_duration = avg(duration_ms)
    by user.email, error.message
| sort by failures desc
```

**Question**: "What's the P99 latency for AI parsing by message length?"

```apl
['cleverbill']
| where ai.parse_duration_ms != null
| summarize 
    p99 = percentile(ai.parse_duration_ms, 99),
    p50 = percentile(ai.parse_duration_ms, 50)
    by bin(ai.message_length, 50)
| render timechart
```

**Question**: "Which users have the most parsing failures?"

```apl
['cleverbill']
| where parsing.success == false
| summarize 
    failures = count(),
    sample_messages = take_any(ai.message_length, 3)
    by user.email, parsing.reason
| where failures > 5
| sort by failures desc
```

## Real Debugging Scenario

### The Problem
User reports: "I sent an expense via WhatsApp but it didn't save"

### Traditional Debugging (30+ minutes)
1. Ask user for timestamp ➜ "around 2pm"
2. Ask user for phone number ➜ privacy concerns
3. grep through logs for 2pm
4. Find 1000+ log lines
5. Try to piece together what happened
6. Still not sure if it was AI parsing, DB, or cache issue
7. Can't reproduce

### Wide Events Debugging (30 seconds)
```apl
['cleverbill']
| where user.email == "user@example.com"
| where _time between (datetime(2024-12-30T14:00:00Z) .. datetime(2024-12-30T15:00:00Z))
| where transaction.type == "expense"
| project 
    _time,
    outcome,
    parsing.success,
    database.success,
    database.operation,
    error.message
```

**Result**: Found it! `database.success = false`, `error.message = "Account not found"`

**Root cause**: User's default account was deleted but WhatsApp still tried to use it.

**Fix**: Add validation before creating transaction.

## Tail Sampling in Action

This request was logged because:
- `force_log: true` (we called `mark_for_logging()` for WhatsApp endpoints)
- `sampling_reason: "critical_endpoint"`

If this was a regular successful request to a non-critical endpoint, it would have had a 5% chance of being logged (to save costs).

But we **ALWAYS** log:
- ✅ Errors (outcome = "error")
- ✅ Slow requests (duration_ms > 2000)
- ✅ Critical endpoints (WhatsApp webhooks)
- ✅ VIP users (is_superuser = true)

This means you **never lose important events**, but you also **don't pay for every single request**.

## Cost Comparison

### Without Sampling
- 1M requests/day
- ~2KB per event
- **2GB/day** = **60GB/month**

### With Tail Sampling (5% + always errors/slow)
- 50K sampled (5%)
- 10K errors (1% error rate - always logged)
- 5K slow (0.5% - always logged)
- **65K events/day**
- **~130MB/day** = **4GB/month**

**Savings**: 93% reduction while keeping ALL important events!

## Next Steps

1. **Start your app** with Axiom configured
2. **Make a request** (e.g., send a WhatsApp message)
3. **Open Axiom** and see your wide events
4. **Run queries** to explore your data
5. **Set up alerts** for errors or slow requests
6. **Create dashboards** for key metrics

See `AXIOM_SETUP.md` for complete setup instructions.

