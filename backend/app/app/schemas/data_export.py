from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field


class DataExportStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    expired = "expired"


class DataExportStatusResponse(BaseModel):
    id: UUID
    status: DataExportStatus
    progress_pct: int
    progress_step: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    error: str | None = None
    filename: str

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def download_available(self) -> bool:
        if self.status != DataExportStatus.ready:
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)


class DataExportDownloadResponse(BaseModel):
    url: str
    expires_in: int
    filename: str
