# 🔧 Fix: Not Seeing Expenses/Data Endpoint Logs

## The Problem

You're not seeing logs for GET/POST requests to `/expenses` or `/data` endpoints, but the test script worked.

**Root Cause:** Tail sampling is working as designed! Only **5% of successful requests** are logged by default.

**Why?** To save costs while keeping important events (errors, slow requests, VIP users).

---

## ✅ Quick Fix: Log Everything (Temporarily)

### Step 1: Update Your .env File

Add or update this line in your `.env` file:

```bash
# Log ALL requests (100%)
AXIOM_SAMPLE_RATE=1.0
```

### Step 2: Restart Your App

```bash
# Stop your app (Ctrl+C)
# Then restart:
cd /Users/alexpc/dev/toston/lake/backend/app
uvicorn app.main:app --reload
```

### Step 3: Make Requests

```bash
# Make a GET request
curl http://localhost:8000/api/v1/expenses/getAll

# Make another one
curl http://localhost:8000/api/v1/utils/health-check/
```

### Step 4: Wait & Check Axiom

- Wait **10 seconds**
- Go to Axiom: https://app.axiom.co/
- Set time range: **"Last 15 minutes"**
- Run query:

```apl
['cleverbill'] | where _time > ago(15m) | sort by _time desc | take 20
```

**You should now see ALL your requests!** ✅

---

## 🎯 Alternative: Trigger Events That Are ALWAYS Logged

Instead of logging everything, you can trigger requests that are always logged:

### Method 1: Trigger Errors (Always Logged)

Errors bypass sampling and are ALWAYS logged:

```bash
# Trigger a 404 error
curl http://localhost:8000/api/v1/expenses/99999999

# Trigger a 401 error (if you have auth)
curl http://localhost:8000/api/v1/expenses/getAll

# Trigger a 422 validation error
curl -X POST http://localhost:8000/api/v1/expenses/ \
  -H "Content-Type: application/json" \
  -d '{"invalid": "data"}'
```

Check Axiom:
```apl
['cleverbill'] | where outcome == "error" | sort by _time desc
```

### Method 2: Make Slow Requests (Always Logged)

Requests over 2000ms are always logged. The dashboard data endpoint with large date ranges might be slow:

```bash
# Request a full year of data (might be slow)
curl "http://localhost:8000/api/v2/data/year/2024"
```

Check Axiom:
```apl
['cleverbill'] | where duration_ms > 2000 | sort by _time desc
```

### Method 3: Use WhatsApp Endpoint (Always Logged)

The WhatsApp webhook is marked as critical and always logged:

```bash
# If you have WhatsApp configured, send a message
# It will always be logged
```

---

## 📊 Understanding Sampling

### What Gets Logged

| Request Type | Logged? | Reason |
|--------------|---------|--------|
| ✅ Errors (4xx, 5xx) | 100% | Critical for debugging |
| ✅ Slow (>2000ms) | 100% | Performance monitoring |
| ✅ WhatsApp webhooks | 100% | Critical business flow |
| ✅ VIP/Superusers | 100% | Important users |
| ❓ Successful requests | 5% | Random sample (configurable) |

### Why Sample?

**Cost savings!** At 1M requests/day:

- **Without sampling:** ~60GB/month → $$$
- **With 5% sampling:** ~4GB/month → Free tier! ✅

You still keep:
- ALL errors
- ALL slow requests  
- ALL critical endpoints
- Random sample of normal traffic

---

## 🧪 Test Script

Run this to verify logging is working:

```bash
cd /Users/alexpc/dev/toston/lake/backend/app
python test_real_requests.py
```

This will:
1. Make successful requests (sampled)
2. Trigger errors (always logged)
3. Tell you what to look for in Axiom

---

## 🎚️ Sampling Configurations

Choose what works for you:

### Development (Log Everything)
```bash
# In .env
AXIOM_SAMPLE_RATE=1.0
```

### Staging (Log Most Things)
```bash
# In .env
AXIOM_SAMPLE_RATE=0.5  # 50%
```

### Production (Cost-Effective)
```bash
# In .env
AXIOM_SAMPLE_RATE=0.05  # 5% (default)
```

### Production (Aggressive Cost Savings)
```bash
# In .env
AXIOM_SAMPLE_RATE=0.01  # 1%
```

**Remember:** Errors and slow requests are ALWAYS logged regardless of this setting!

---

## 🔍 Verify Middleware is Working

### Check 1: Startup Message

When you start your app, you should see:

```
🚀 Starting application...
📊 Initializing Axiom logging to dataset: cleverbill
✅ Axiom logging initialized successfully
```

### Check 2: Response Headers

Make a request and check for the `X-Request-ID` header:

```bash
curl -i http://localhost:8000/api/v1/utils/health-check/
```

Look for:
```
X-Request-ID: abc-123-def-456-...
```

If you see this header, the middleware is running! ✅

### Check 3: Terminal Output

With `AXIOM_ENABLED=false`, events print to stdout:

```bash
# Temporarily in .env
AXIOM_ENABLED=false
```

Restart app and make requests. You should see JSON events printed to console.

---

## 🐛 Still Not Working?

### Check Your Endpoint Updates

Verify the endpoints have `Request` parameter:

```python
# Should look like this:
@router.get("/getAll")
async def read_expenses(
    request: Request,  # ← This must be here!
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
):
    # Add user context
    enrich_event(request, user={...})  # ← This must be called
```

### Check Middleware Order

In `main.py`, WideEventsMiddleware should be added BEFORE CORS:

```python
# In main.py:

# Wide events middleware FIRST
app.add_middleware(WideEventsMiddleware, ...)

# Then CORS
app.add_middleware(CORSMiddleware, ...)
```

### Check for Exceptions

Look for errors in app logs when making requests:

```
❌ Error in periodic flush: ...
❌ Failed to send events to Axiom: ...
```

---

## 💡 Recommended Approach

For testing/development:

1. **Set `AXIOM_SAMPLE_RATE=1.0`** to log everything
2. **Make requests** to your endpoints
3. **Verify they appear** in Axiom
4. **Once confirmed working**, lower to `0.05` for production

For production:

1. Keep `AXIOM_SAMPLE_RATE=0.05` (default)
2. Monitor errors (always logged)
3. Set up alerts for errors
4. Use Axiom's powerful queries to find patterns

---

## 🎉 Summary

**The logging IS working** - test events proved it!

**The issue:** Sampling is doing its job (only 5% of successful requests)

**The fix:** Set `AXIOM_SAMPLE_RATE=1.0` in your .env file

**Remember:** Errors and slow requests are ALWAYS logged, so you never miss important events! 🚀


