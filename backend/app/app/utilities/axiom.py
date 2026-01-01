"""
Axiom logging client with support for wide events pattern
Based on principles from https://loggingsucks.com/
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional
from datetime import datetime
import httpx


class AxiomClient:
    """
    Client for sending structured logs to Axiom.co
    
    Implements batching and async sending for performance.
    """
    
    def __init__(
        self,
        dataset: str,
        api_token: str,
        batch_size: int = 100,
        flush_interval: float = 5.0,
        enabled: bool = True
    ):
        self.dataset = dataset
        self.api_token = api_token
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.enabled = enabled
        
        self.base_url = "https://api.axiom.co/v1"
        self.ingest_url = f"{self.base_url}/datasets/{dataset}/ingest"
        
        self._buffer: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        
        if not enabled:
            print("⚠️  Axiom logging is disabled")
    
    async def start(self):
        """Initialize the HTTP client and start the flush task"""
        if not self.enabled:
            return
            
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        
        # Start background flush task
        self._flush_task = asyncio.create_task(self._periodic_flush())
    
    async def stop(self):
        """Flush remaining events and cleanup"""
        if not self.enabled:
            return
            
        # Cancel the flush task
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # Flush any remaining events
        await self.flush()
        
        # Close the HTTP client
        if self._client:
            await self._client.aclose()
    
    async def log(self, event: Dict[str, Any]) -> None:
        """
        Add an event to the buffer.
        
        Events are automatically flushed when:
        - Buffer reaches batch_size
        - flush_interval seconds have passed (background task)
        - Manually calling flush()
        """
        if not self.enabled:
            # Still log to stdout for development
            print(json.dumps(event, default=str))
            return
        
        # Ensure timestamp is present
        if "_time" not in event:
            event["_time"] = datetime.utcnow().isoformat() + "Z"
        
        async with self._lock:
            self._buffer.append(event)
            
            # Auto-flush if buffer is full
            if len(self._buffer) >= self.batch_size:
                await self._flush_buffer()
    
    async def flush(self) -> None:
        """Manually flush all buffered events"""
        if not self.enabled:
            return
            
        async with self._lock:
            await self._flush_buffer()
    
    async def _flush_buffer(self) -> None:
        """Internal method to flush buffer (must be called with lock held)"""
        if not self._buffer or not self._client:
            return
        
        events = self._buffer.copy()
        self._buffer.clear()
        
        try:
            response = await self._client.post(
                self.ingest_url,
                json=events,
            )
            
            if response.status_code == 200:
                result = response.json()
                ingested = result.get("ingested", 0)
                failed = result.get("failed", 0)
                
                if failed > 0:
                    print(f"⚠️  Axiom: {ingested} events ingested, {failed} failed")
            else:
                print(f"❌ Failed to send events to Axiom: {response.status_code} - {response.text}")
                # Could implement retry logic here
                
        except Exception as e:
            print(f"❌ Error sending events to Axiom: {str(e)}")
            # Could implement retry logic here
    
    async def _periodic_flush(self) -> None:
        """Background task that flushes buffer periodically"""
        while True:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in periodic flush: {str(e)}")


# Global singleton instance
_axiom_client: Optional[AxiomClient] = None


def initialize_axiom(
    dataset: str,
    api_token: str,
    batch_size: int = 100,
    flush_interval: float = 5.0,
    enabled: bool = True
) -> AxiomClient:
    """Initialize the global Axiom client"""
    global _axiom_client
    
    _axiom_client = AxiomClient(
        dataset=dataset,
        api_token=api_token,
        batch_size=batch_size,
        flush_interval=flush_interval,
        enabled=enabled
    )
    
    return _axiom_client


def get_axiom_client() -> Optional[AxiomClient]:
    """Get the global Axiom client instance"""
    return _axiom_client


async def log_event(event: Dict[str, Any]) -> None:
    """Convenience function to log an event using the global client"""
    if _axiom_client:
        await _axiom_client.log(event)

