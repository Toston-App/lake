"""Shared service exceptions and HTTP translation helpers."""

from typing import Any

from fastapi import HTTPException


class ServiceError(Exception):
    """Base exception for reusable service-layer failures."""

    status_code = 400
    # Recorded as the request's operation outcome when set.
    event_outcome: str | None = None

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

    def detail(self) -> Any:
        return self.message

    def headers(self) -> dict[str, str]:
        return {}


class NotFoundError(ServiceError):
    status_code = 404


class PermissionDeniedError(ServiceError):
    status_code = 403


class DomainError(ServiceError):
    status_code = 400


class ConflictError(ServiceError):
    status_code = 409


def raise_http(exc: ServiceError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.detail(),
        headers=exc.headers() or None,
    )
