from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings
from app.models.user import User

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class R2NotConfigured(RuntimeError):
    """Raised when export R2 settings are missing."""


def is_configured() -> bool:
    return bool(
        settings.R2_ACCESS_KEY_ID
        and settings.R2_SECRET_ACCESS_KEY
        and settings.R2_ENDPOINT
        and settings.R2_BUCKET_NAME
        and settings.R2_PATH
    )


def _require_configured() -> None:
    if not is_configured():
        raise R2NotConfigured("User export storage is not configured")


def _object_prefix() -> str:
    prefix = settings.R2_PATH.strip().strip("/")
    return prefix


def object_key(user: User, export_id: UUID) -> str:
    owner = user.uuid or f"user-{user.id}"
    return f"{_object_prefix()}/{owner}/{export_id}.xlsx"


def _endpoint_url() -> str:
    endpoint = (settings.R2_ENDPOINT or "").strip()
    if endpoint and not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"
    return endpoint


def _client():
    _require_configured()
    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name=settings.R2_REGION or "auto",
        config=Config(signature_version="s3v4"),
    )


def _upload_file_sync(key: str, path: Path, *, user_uuid: str | None, export_id: str) -> None:
    client = _client()
    extra = {
        "ContentType": XLSX_CONTENT_TYPE,
        "Metadata": {
            "user-uuid": user_uuid or "",
            "export-id": export_id,
        },
    }
    client.upload_file(str(path), settings.R2_BUCKET_NAME, key, ExtraArgs=extra)


def _delete_object_sync(key: str) -> None:
    client = _client()
    try:
        client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
    except ClientError:
        # Missing object is fine; the job may have failed before upload.
        return


def _presign_get_sync(key: str, filename: str, expires_in: int) -> str:
    client = _client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
            "ResponseContentType": XLSX_CONTENT_TYPE,
        },
        ExpiresIn=expires_in,
    )


async def upload_file(
    key: str, path: Path, *, user_uuid: str | None, export_id: UUID
) -> None:
    await asyncio.to_thread(
        _upload_file_sync,
        key,
        path,
        user_uuid=user_uuid,
        export_id=str(export_id),
    )


async def delete_object(key: str) -> None:
    await asyncio.to_thread(_delete_object_sync, key)


async def presign_get(key: str, filename: str, expires_in: int) -> str:
    return await asyncio.to_thread(_presign_get_sync, key, filename, expires_in)
