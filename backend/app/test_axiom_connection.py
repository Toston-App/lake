#!/usr/bin/env python
"""
Quick test script to verify Axiom connection works
Run this to test if your Axiom setup is working
"""

import asyncio
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, '/Users/alexpc/dev/toston/lake/backend/app')

from app.core.config import settings
from app.utilities.axiom import initialize_axiom


async def test_axiom():
    print("🧪 Testing Axiom Connection")
    print("=" * 60)
    
    # Show config
    print(f"Dataset: {settings.AXIOM_DATASET}")
    print(f"Token: {settings.AXIOM_API_TOKEN[:15]}..." if settings.AXIOM_API_TOKEN else "NOT SET")
    print(f"Enabled: {settings.AXIOM_ENABLED}")
    print("=" * 60)
    
    if not settings.AXIOM_API_TOKEN:
        print("❌ ERROR: AXIOM_API_TOKEN not set in environment")
        return False
    
    # Initialize client
    print("\n📊 Initializing Axiom client...")
    client = initialize_axiom(
        dataset=settings.AXIOM_DATASET,
        api_token=settings.AXIOM_API_TOKEN,
        batch_size=1,  # Send immediately for testing
        flush_interval=1.0,
        enabled=True,
    )
    
    if not client:
        print("❌ Failed to initialize client")
        return False
    
    print("✅ Client initialized")
    
    # Start client
    print("\n🚀 Starting client...")
    await client.start()
    print("✅ Client started")
    
    # Send test events
    print("\n📤 Sending test events...")
    
    test_events = [
        {
            "message": "Test event #1 - Simple message",
            "test": True,
            "source": "test_script",
            "_time": datetime.utcnow().isoformat() + "Z",
        },
        {
            "message": "Test event #2 - With more context",
            "test": True,
            "source": "test_script",
            "user": {"email": "test@example.com", "id": 999},
            "http": {"method": "TEST", "path": "/test"},
            "_time": datetime.utcnow().isoformat() + "Z",
        },
        {
            "message": "Test event #3 - Simulating error",
            "test": True,
            "source": "test_script",
            "outcome": "error",
            "error": {"type": "TestError", "message": "This is a test error"},
            "_time": datetime.utcnow().isoformat() + "Z",
        },
    ]
    
    for i, event in enumerate(test_events, 1):
        await client.log(event)
        print(f"  ✅ Event {i} logged: {event['message']}")
    
    # Force flush
    print("\n⏳ Flushing events to Axiom (this may take a few seconds)...")
    await asyncio.sleep(2)
    await client.flush()
    print("✅ Events flushed")
    
    # Wait a bit more
    print("\n⏳ Waiting for Axiom to process...")
    await asyncio.sleep(3)
    
    # Cleanup
    print("\n🧹 Cleaning up...")
    await client.stop()
    print("✅ Client stopped")
    
    print("\n" + "=" * 60)
    print("✅ TEST COMPLETE!")
    print("=" * 60)
    print("\n📊 Check Axiom now:")
    print(f"   1. Go to: https://app.axiom.co/")
    print(f"   2. Select dataset: {settings.AXIOM_DATASET}")
    print(f"   3. Set time range: Last 15 minutes")
    print(f"   4. Run query: ['cleverbill'] | where test == true")
    print("\n   You should see 3 test events!")
    print("\n💡 If you don't see them:")
    print("   - Check the time range (top-right)")
    print("   - Verify dataset name matches")
    print("   - Try query: ['cleverbill'] | take 10")
    print("   - See TROUBLESHOOTING_AXIOM.md for more help")
    
    return True


if __name__ == "__main__":
    try:
        success = asyncio.run(test_axiom())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


