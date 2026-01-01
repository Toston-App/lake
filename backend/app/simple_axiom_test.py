#!/usr/bin/env python
"""
Simple Axiom connection test
"""

import asyncio
import os
import httpx
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

AXIOM_TOKEN = os.getenv('AXIOM_API_TOKEN')
AXIOM_DATASET = os.getenv('AXIOM_DATASET', 'cleverbill')


async def test_axiom_connection():
    print("🧪 Testing Axiom Connection")
    print("=" * 60)
    
    if not AXIOM_TOKEN:
        print("❌ ERROR: AXIOM_API_TOKEN not found in environment")
        print("   Make sure your .env file has: AXIOM_API_TOKEN=xaat-...")
        return False
    
    print(f"✅ Token found: {AXIOM_TOKEN[:15]}...")
    print(f"✅ Dataset: {AXIOM_DATASET}")
    print("=" * 60)
    
    # Create test events
    test_events = [
        {
            "message": "🧪 Test event #1 from simple test",
            "test": True,
            "test_run": datetime.utcnow().isoformat(),
            "_time": datetime.utcnow().isoformat() + "Z",
        },
        {
            "message": "🧪 Test event #2 with context",
            "test": True,
            "test_run": datetime.utcnow().isoformat(),
            "http": {"method": "TEST", "path": "/test"},
            "user": {"email": "test@example.com"},
            "_time": datetime.utcnow().isoformat() + "Z",
        },
        {
            "message": "🧪 Test event #3 simulating error",
            "test": True,
            "test_run": datetime.utcnow().isoformat(),
            "outcome": "error",
            "error": {"type": "TestError", "message": "Test error for visibility"},
            "_time": datetime.utcnow().isoformat() + "Z",
        },
    ]
    
    # Send to Axiom
    url = f"https://api.axiom.co/v1/datasets/{AXIOM_DATASET}/ingest"
    headers = {
        "Authorization": f"Bearer {AXIOM_TOKEN}",
        "Content-Type": "application/json",
    }
    
    print(f"\n📤 Sending {len(test_events)} test events to Axiom...")
    print(f"   URL: {url}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=test_events, headers=headers)
            
            print(f"\n📊 Response Status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ SUCCESS!")
                print(f"   Ingested: {result.get('ingested', 0)}")
                print(f"   Failed: {result.get('failed', 0)}")
                print(f"   Processed Bytes: {result.get('processedBytes', 0)}")
                
                if result.get('failed', 0) > 0:
                    print(f"\n⚠️  Some events failed:")
                    print(f"   Failures: {result.get('failures', [])}")
                
                print("\n" + "=" * 60)
                print("✅ TEST PASSED!")
                print("=" * 60)
                print("\n📊 Check Axiom now:")
                print(f"   1. Go to: https://app.axiom.co/")
                print(f"   2. Select dataset: {AXIOM_DATASET}")
                print(f"   3. ⚠️  IMPORTANT: Set time range to 'Last 15 minutes'")
                print(f"      (Click the time selector in top-right corner)")
                print(f"   4. Run this query:")
                print(f"\n      ['cleverbill'] | where test == true | sort by _time desc\n")
                print(f"   You should see 3 test events with 🧪 emoji!")
                print("\n💡 Pro tip: If you don't see them:")
                print("   - Double-check the time range (most common issue!)")
                print("   - Try: ['cleverbill'] | take 10")
                print("   - Refresh the page")
                
                return True
                
            elif response.status_code == 401:
                print("❌ AUTHENTICATION FAILED")
                print("   Your API token is invalid or expired")
                print("   1. Go to: https://app.axiom.co/settings/tokens")
                print("   2. Create a new token with 'Ingest' permission")
                print("   3. Update AXIOM_API_TOKEN in your .env file")
                return False
                
            elif response.status_code == 404:
                print(f"❌ DATASET NOT FOUND")
                print(f"   Dataset '{AXIOM_DATASET}' does not exist")
                print("   1. Go to: https://app.axiom.co/datasets")
                print(f"   2. Create a dataset named: {AXIOM_DATASET}")
                print("   3. Or update AXIOM_DATASET in your .env file")
                return False
                
            else:
                print(f"❌ UNEXPECTED RESPONSE: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
    except httpx.TimeoutException:
        print("❌ TIMEOUT")
        print("   Request timed out. Check your internet connection.")
        return False
    except httpx.ConnectError:
        print("❌ CONNECTION ERROR")
        print("   Could not connect to Axiom. Check your internet connection.")
        return False
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(test_axiom_connection())
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n❌ Test cancelled")
        exit(1)


