#!/usr/bin/env python
"""
Test that real API requests are being logged
"""

import asyncio
import httpx
import time

BASE_URL = "http://localhost:8000"

async def test_api_logging():
    print("🧪 Testing Real API Request Logging")
    print("=" * 60)
    print("⚠️  Make sure your FastAPI app is running first!")
    print("   Run: uvicorn app.main:app --reload")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        # Test 1: Health check (should be sampled)
        print("\n📤 Test 1: Making health check request...")
        try:
            response = await client.get(f"{BASE_URL}/api/v1/utils/health-check/")
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.json()}")
            print(f"   ✅ Request successful")
        except Exception as e:
            print(f"   ❌ Error: {str(e)}")
            print(f"   Make sure your app is running!")
            return False
        
        # Test 2: Trigger an error (always logged - 404)
        print("\n📤 Test 2: Triggering 404 error (always logged)...")
        try:
            response = await client.get(f"{BASE_URL}/api/v1/expenses/99999999")
            print(f"   Status: {response.status_code}")
        except Exception as e:
            print(f"   Got error as expected: {response.status_code}")
        print(f"   ✅ Error triggered (this should ALWAYS be logged)")
        
        # Test 3: Trigger validation error (always logged - 422)
        print("\n📤 Test 3: Triggering validation error (always logged)...")
        try:
            response = await client.post(
                f"{BASE_URL}/api/v1/expenses/",
                json={"invalid": "data"}
            )
            print(f"   Status: {response.status_code}")
        except Exception as e:
            print(f"   Got error as expected")
        print(f"   ✅ Validation error triggered (this should ALWAYS be logged)")
        
        # Make multiple successful requests
        print("\n📤 Test 4: Making 10 successful requests...")
        print("   (These are sampled at 5%, so only ~0-1 might be logged)")
        for i in range(10):
            try:
                response = await client.get(f"{BASE_URL}/api/v1/utils/health-check/")
                print(f"   Request {i+1}/10: {response.status_code}")
            except Exception as e:
                print(f"   Request {i+1}/10 failed: {str(e)}")
        
        print("\n⏳ Waiting 10 seconds for events to be batched and sent...")
        for i in range(10, 0, -1):
            print(f"   {i}...", end=" ", flush=True)
            await asyncio.sleep(1)
        print("\n")
        
        print("=" * 60)
        print("✅ TESTS COMPLETE!")
        print("=" * 60)
        print("\n📊 Check Axiom now:")
        print("   1. Go to: https://app.axiom.co/")
        print("   2. Select dataset: cleverbill")
        print("   3. Set time range: 'Last 15 minutes'")
        print("   4. Run this query:\n")
        print("      ['cleverbill'] | where _time > ago(15m) | sort by _time desc\n")
        print("   You SHOULD see:")
        print("   - ✅ The 404 error (Test 2)")
        print("   - ✅ The 422 error (Test 3)")
        print("   - ❓ Maybe 0-1 health checks (Test 4 - only 5% sampled)")
        print("\n💡 If you don't see the errors:")
        print("   - Check the time range")
        print("   - Make sure your app showed 'Axiom logging initialized'")
        print("   - Check app logs for error messages")
        
        return True

if __name__ == "__main__":
    try:
        success = asyncio.run(test_api_logging())
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n❌ Test cancelled")
        exit(1)


