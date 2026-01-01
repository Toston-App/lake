"""
Wide Events Middleware for FastAPI
Implements the wide events pattern from https://loggingsucks.com/

Key principles:
1. One comprehensive event per request with ALL context
2. High-cardinality, high-dimensionality data
3. Build the event throughout the request lifecycle
4. Emit once at the end
5. Tail sampling: always keep errors, slow requests, VIP users
"""

import time
import uuid
import random
from typing import Any, Dict, Optional
from datetime import datetime

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.utilities.axiom import get_axiom_client


class WideEventsMiddleware(BaseHTTPMiddleware):
    """
    Middleware that creates one comprehensive "wide event" per request.
    
    The event is built throughout the request lifecycle and sent to Axiom
    at the end with full context for powerful querying and debugging.
    """
    
    def __init__(
        self,
        app: ASGIApp,
        service_name: str,
        service_version: str,
        deployment_id: Optional[str] = None,
        region: Optional[str] = None,
        environment: str = "production",
        sample_rate: float = 0.05,  # Sample 5% of successful requests
        slow_request_threshold_ms: int = 2000,  # P99 threshold
    ):
        super().__init__(app)
        self.service_name = service_name
        self.service_version = service_version
        self.deployment_id = deployment_id
        self.region = region
        self.environment = environment
        self.sample_rate = sample_rate
        self.slow_request_threshold_ms = slow_request_threshold_ms
    
    async def dispatch(self, request: Request, call_next):
        """Process request and build wide event"""
        start_time = time.time()
        start_timestamp = datetime.utcnow()
        
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Initialize the wide event with request context
        event: Dict[str, Any] = {
            # Timestamps and identifiers
            "_time": start_timestamp.isoformat() + "Z",
            "request_id": request_id,
            "trace_id": request.headers.get("X-Trace-ID", request_id),
            
            # Service metadata
            "service": self.service_name,
            "version": self.service_version,
            "environment": self.environment,
            "deployment_id": self.deployment_id,
            "region": self.region,
            
            # HTTP request details
            "http": {
                "method": request.method,
                "path": request.url.path,
                "url": str(request.url),
                "scheme": request.url.scheme,
                "query_params": dict(request.query_params),
                "user_agent": request.headers.get("user-agent"),
                "referer": request.headers.get("referer"),
                "host": request.headers.get("host"),
                "content_type": request.headers.get("content-type"),
            },
            
            # Network details
            "network": {
                "client_ip": request.client.host if request.client else None,
                "client_port": request.client.port if request.client else None,
            },
        }
        
        # Store event in request state for enrichment by handlers
        request.state.wide_event = event
        request.state.request_id = request_id
        
        # Process the request
        error = None
        response = None
        
        try:
            response = await call_next(request)
            event["outcome"] = "success"
            event["http"]["status_code"] = response.status_code
            
            # Add response headers metadata
            event["http"]["content_length"] = response.headers.get("content-length")
            
        except Exception as e:
            error = e
            event["outcome"] = "error"
            event["http"]["status_code"] = 500
            
            # Capture detailed error information
            event["error"] = {
                "type": type(e).__name__,
                "message": str(e),
                "module": type(e).__module__,
            }
            
            # Try to capture stack trace for serious errors
            import traceback
            event["error"]["stack_trace"] = traceback.format_exc()
            
        finally:
            # Calculate request duration
            duration_ms = (time.time() - start_time) * 1000
            event["duration_ms"] = round(duration_ms, 2)
            
            # Add performance classification
            if duration_ms < 100:
                event["performance"] = "fast"
            elif duration_ms < 500:
                event["performance"] = "normal"
            elif duration_ms < 2000:
                event["performance"] = "slow"
            else:
                event["performance"] = "very_slow"
            
            # Tail sampling: decide whether to keep this event
            should_log = self._should_sample(event, duration_ms)
            
            if should_log:
                # Send to Axiom
                axiom_client = get_axiom_client()
                if axiom_client:
                    await axiom_client.log(event)
            
            # Re-raise the error if one occurred
            if error:
                raise error
        
        # Add request ID to response headers for tracing
        response.headers["X-Request-ID"] = request_id
        
        return response
    
    def _should_sample(self, event: Dict[str, Any], duration_ms: float) -> bool:
        """
        Tail sampling logic: intelligently decide which events to keep
        
        Always keep:
        1. Errors (4xx, 5xx)
        2. Slow requests (above threshold)
        3. VIP/Enterprise users (if marked in event)
        4. Specific endpoints (if marked in event)
        
        Random sample the rest based on sample_rate
        """
        
        # Always keep errors
        if event.get("outcome") == "error":
            return True
        
        status_code = event.get("http", {}).get("status_code", 200)
        if status_code >= 400:
            return True
        
        # Always keep slow requests (above p99 threshold)
        if duration_ms > self.slow_request_threshold_ms:
            event["sampling_reason"] = "slow_request"
            return True
        
        # Always keep VIP/Enterprise users
        user_context = event.get("user", {})
        if user_context.get("is_superuser") or user_context.get("subscription_tier") == "enterprise":
            event["sampling_reason"] = "vip_user"
            return True
        
        # Always keep if explicitly marked for debugging
        if event.get("force_log") or event.get("debug_mode"):
            event["sampling_reason"] = "debug_mode"
            return True
        
        # Always keep WhatsApp webhook requests (critical business flow)
        path = event.get("http", {}).get("path", "")
        if "/whatsapp/webhook" in path:
            event["sampling_reason"] = "critical_endpoint"
            return True
        
        # Random sample the rest
        if random.random() < self.sample_rate:
            event["sampling_reason"] = "random_sample"
            return True
        
        event["sampling_reason"] = "not_sampled"
        return False


def enrich_event(request: Request, **kwargs: Any) -> None:
    """
    Convenience function to add context to the wide event.
    
    Use this in your endpoints to add business context:
    
    Example:
        enrich_event(
            request,
            user={
                "id": user.id,
                "email": user.email,
                "subscription": user.subscription,
                "account_age_days": (datetime.now() - user.created_at).days,
            },
            business={
                "transaction_type": "expense",
                "amount_cents": 12500,
                "category": "food",
            }
        )
    """
    if hasattr(request.state, "wide_event"):
        request.state.wide_event.update(kwargs)


def mark_for_logging(request: Request) -> None:
    """Force this request to be logged regardless of sampling"""
    if hasattr(request.state, "wide_event"):
        request.state.wide_event["force_log"] = True


def get_request_id(request: Request) -> Optional[str]:
    """Get the request ID for this request"""
    return getattr(request.state, "request_id", None)

