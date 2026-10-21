"""Shared service exceptions and HTTP translation helpers."""

from fastapi import HTTPException


class ServiceError(Exception):
    """Base exception for reusable service-layer failures."""

    status_code = 400

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotFoundError(ServiceError):
    status_code = 404


class PermissionDeniedError(ServiceError):
    status_code = 403


class DomainError(ServiceError):
    status_code = 400


def raise_http(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message)
