# 🔍 Axiom Logging Troubleshooting Guide

## Not Seeing Data in Axiom? Follow These Steps

### Step 1: Check Your Environment Variables

Run these commands to verify your configuration:

```bash
# Check if variables are set
echo "AXIOM_API_TOKEN: $AXIOM_API_TOKEN"
echo "AXIOM_DATASET: $AXIOM_DATASET"
echo "AXIOM_ENABLED: $AXIOM_ENABLED"
```

**Expected output:**
```
AXIOM_API_TOKEN: xaat-xxxxxxxxxx  (should start with xaat-)
AXIOM_DATASET: cleverbill  (or your dataset name)
AXIOM_ENABLED: true  (or empty - auto-enabled if token is set)
```

**❌ If token is empty:**
1. Check your `.env` file has `AXIOM_API_TOKEN=xaat-your-token`
2. Restart your application

---

### Step 2: Check Application Startup Logs

Look for these messages when your app starts:

**✅ Success:**
```
🚀 Starting application...
📊 Initializing Axiom logging to dataset: cleverbill
✅ Axiom logging initialized successfully
```

**❌ If you see:**
```
⚠️ Axiom API token not configured - logging will be to stdout only
```
→ Your token is not set. Go back to Step 1.

**❌ If you see:**
```
⚠️ Axiom logging is disabled
```
→ Check `AXIOM_ENABLED` is set to `true` or remove it (auto-enabled with token)

---

### Step 3: Make a Test Request

Make a simple request to trigger logging:

```bash
curl http://localhost:8000/api/v1/utils/health-check/
```

You should see in your terminal:
- The request being processed
- No error messages

---

### Step 4: Wait for Batch Flush

Events are sent in batches. Wait **5-10 seconds** after making requests.

The default flush settings:
- **Every 100 events** OR
- **Every 5 seconds**

After 10 seconds, events should be in Axiom.

---

### Step 5: Check Axiom UI Settings

#### 5.1 Verify Dataset Selection

In Axiom UI:
1. Look at the top of the page
2. Make sure you're viewing the correct dataset (e.g., "cleverbill")
3. Try switching datasets if you have multiple

#### 5.2 Check Time Range

**This is the most common issue!**

In Axiom UI:
1. Look at the top-right for the time range selector
2. Click it and select **"Last 1 hour"** or **"Last 24 hours"**
3. If you just started, use **"Last 15 minutes"**

Default is often "Last 1 hour" which might not show brand new events.

---

### Step 6: Test Token Manually

Test your Axiom token with curl:

```bash
curl -X POST https://api.axiom.co/v1/datasets/YOUR_DATASET/ingest \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[{
    "message": "test from curl",
    "_time": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
  }]'
```

**Replace:**
- `YOUR_DATASET` with your dataset name
- `YOUR_TOKEN` with your API token

**Expected response:**
```json
{"ingested": 1, "failed": 0, "failures": [], "processedBytes": 123, "blocksCreated": 1, "walLength": 1}
```

**✅ If successful:** Your token works! Problem is with the app.
**❌ If error:** Check token and dataset name.

---

### Step 7: Check Application Logs for Errors

Look for error messages like:

```
❌ Failed to send events to Axiom: 401 - Unauthorized
❌ Failed to send events to Axiom: 404 - Not Found
❌ Error sending events to Axiom: ...
```

**Common errors:**

| Error | Cause | Solution |
|-------|-------|----------|
| 401 Unauthorized | Invalid API token | Check token in Axiom UI, regenerate if needed |
| 404 Not Found | Dataset doesn't exist | Check dataset name spelling |
| 403 Forbidden | Token lacks permissions | Token needs "Ingest" permission |
| Network timeout | Firewall/network issue | Check internet connection |

---

### Step 8: Enable Debug Logging

Temporarily add print statements to see what's happening:

```python
# In your main.py, after axiom_client initialization
axiom_client = get_axiom_client()
if axiom_client:
    print(f"✅ Axiom client initialized: enabled={axiom_client.enabled}")
    print(f"   Dataset: {axiom_client.dataset}")
    print(f"   Base URL: {axiom_client.base_url}")
else:
    print("❌ Axiom client is None!")
```

---

### Step 9: Force Immediate Flush

To test without waiting for batch timer, modify your code temporarily:

```python
# In any endpoint, after enrich_event():
from app.utilities.axiom import get_axiom_client

axiom_client = get_axiom_client()
if axiom_client:
    await axiom_client.flush()  # Force immediate send
    print("✅ Flushed events to Axiom")
```

Make a request, then check Axiom immediately.

---

### Step 10: Check for Sampling Issues

Remember: **Not all requests are logged!**

The default sampling:
- ✅ **100% of errors** (4xx, 5xx)
- ✅ **100% of slow requests** (>2000ms)
- ✅ **100% of WhatsApp webhooks**
- ✅ **100% of VIP users**
- ✅ **5% of successful requests**

**To test, trigger an error:**

```bash
# This should always be logged (404 error)
curl http://localhost:8000/api/v1/expenses/99999999
```

Or temporarily disable sampling:

```python
# In .env
AXIOM_SAMPLE_RATE=1.0  # Log 100% of requests
```

Restart app and try again.

---

## 🔧 Quick Fixes

### Fix 1: Restart Application

Sometimes a simple restart helps:

```bash
# Stop your app (Ctrl+C)
# Start again
uvicorn app.main:app --reload
```

### Fix 2: Use stdout for Testing

Temporarily disable Axiom to see events in console:

```python
# In .env
AXIOM_ENABLED=false
```

Events will print to stdout. If you see them there, the issue is with Axiom connection.

### Fix 3: Lower Batch Size

Make events send faster:

```python
# In main.py, startup_event():
axiom_client = initialize_axiom(
    dataset=settings.AXIOM_DATASET,
    api_token=settings.AXIOM_API_TOKEN,
    batch_size=1,  # Send immediately (was 100)
    flush_interval=1.0,  # Flush every second (was 5.0)
    enabled=settings.AXIOM_ENABLED,
)
```

---

## 🎯 Checklist

Go through this checklist:

- [ ] `AXIOM_API_TOKEN` is set in `.env`
- [ ] Token starts with `xaat-`
- [ ] `AXIOM_DATASET` matches dataset in Axiom UI
- [ ] App shows "✅ Axiom logging initialized successfully" on startup
- [ ] Made at least one request
- [ ] Waited 10+ seconds for batch flush
- [ ] Selected correct dataset in Axiom UI
- [ ] Time range in Axiom is "Last 1 hour" or "Last 24 hours"
- [ ] Manual curl test to Axiom API succeeded
- [ ] No error messages in app logs
- [ ] Tried triggering an error (should always be logged)

---

## 🆘 Still Not Working?

### Option 1: Check Axiom Ingestion Stats

In Axiom UI:
1. Go to Settings → Datasets
2. Click your dataset
3. Look at "Ingestion" tab
4. Check if any data has been received

### Option 2: Verify Dataset Exists

In Axiom UI:
1. Click Datasets in left sidebar
2. Verify your dataset (e.g., "cleverbill") exists
3. If not, create it:
   - Click "Create Dataset"
   - Name it exactly as in your `.env`

### Option 3: Check Token Permissions

In Axiom UI:
1. Go to Settings → API Tokens
2. Find your token
3. Verify it has "Ingest" permission
4. If not, create a new token with correct permission

### Option 4: Check Rate Limits

Axiom has rate limits. In Axiom UI:
1. Go to Settings → Usage
2. Check if you hit any limits

### Option 5: Try a Different Browser

Sometimes browser cache causes issues. Try:
- Different browser
- Incognito/private mode
- Hard refresh (Ctrl+Shift+R or Cmd+Shift+R)

---

## 📝 Common Scenarios

### Scenario 1: "I see startup message but no data"

**Likely cause:** Sampling

**Solution:**
```bash
# In .env
AXIOM_SAMPLE_RATE=1.0  # Temporarily log everything
```

Restart app, make requests, check Axiom.

### Scenario 2: "Manual curl works but app doesn't send"

**Likely cause:** Async flush not happening

**Solution:** Check shutdown logs when you stop app:
```
👋 Shutting down application...
📤 Flushing remaining logs to Axiom...
✅ Axiom client closed
```

If not seeing these, add explicit flush in your test.

### Scenario 3: "Data appears 5+ minutes late"

**Likely cause:** Batch not filling up fast enough

**Solution:** Lower batch_size to 10 or flush_interval to 1 second.

### Scenario 4: "Only seeing some requests"

**This is normal!** Default is 5% sampling.

**Solution:** See "Step 10: Check for Sampling Issues" above.

---

## 🧪 Minimal Test Script

Create a test file to verify Axiom works:

```python
# test_axiom.py
import asyncio
from app.utilities.axiom import initialize_axiom
from datetime import datetime

async def test():
    print("🧪 Testing Axiom connection...")
    
    # Initialize client
    client = initialize_axiom(
        dataset="cleverbill",  # Your dataset
        api_token="xaat-xxxxx",  # Your token
        batch_size=1,
        flush_interval=1.0,
        enabled=True,
    )
    
    await client.start()
    print("✅ Client started")
    
    # Send test event
    await client.log({
        "message": "Test from Python",
        "test": True,
        "_time": datetime.utcnow().isoformat() + "Z",
    })
    print("✅ Event logged")
    
    # Wait for flush
    await asyncio.sleep(2)
    print("✅ Waited for flush")
    
    # Cleanup
    await client.stop()
    print("✅ Client stopped - check Axiom!")

if __name__ == "__main__":
    asyncio.run(test())
```

Run it:
```bash
cd backend/app
python test_axiom.py
```

Check Axiom for the test event.

---

## 📞 Need More Help?

1. **Check Axiom Status**: https://status.axiom.co/
2. **Axiom Discord**: https://axiom.co/discord
3. **Axiom Docs**: https://axiom.co/docs
4. **Check app logs**: Look for any error messages

---

## ✅ Success Indicators

You'll know it's working when:

1. App shows "✅ Axiom logging initialized successfully"
2. After making requests and waiting 10 seconds...
3. Axiom UI shows events in your dataset
4. Running a query returns results:

```apl
['your-dataset'] | take 10
```

Good luck! 🚀


