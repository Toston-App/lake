"""API tests for /api/v1/exports."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.models.data_export import DataExport, DataExportStatus
from app.services.user_export import process_export_job
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_test_token
from tests.utils import create_test_account, create_test_expense, create_test_user

PREFIX = "/api/v1/exports"


@pytest.fixture
def mock_r2(monkeypatch: pytest.MonkeyPatch) -> dict:
    upload = AsyncMock()
    delete = AsyncMock()
    presign = AsyncMock(return_value="https://signed.example/file.xlsx")
    monkeypatch.setattr("app.services.r2.is_configured", lambda: True)
    monkeypatch.setattr("app.api.api_v1.endpoints.exports.r2.is_configured", lambda: True)
    monkeypatch.setattr("app.api.api_v1.endpoints.exports.r2.upload_file", upload)
    monkeypatch.setattr("app.api.api_v1.endpoints.exports.r2.delete_object", delete)
    monkeypatch.setattr("app.api.api_v1.endpoints.exports.r2.presign_get", presign)
    monkeypatch.setattr("app.services.user_export.r2.upload_file", upload)
    monkeypatch.setattr("app.services.user_export.r2.delete_object", delete)
    return {"upload": upload, "delete": delete, "presign": presign}


class TestExportAuth:
    async def test_post_requires_auth(self, unauth_client: AsyncClient, mock_r2):
        resp = await unauth_client.post(PREFIX)
        assert resp.status_code == 401

    async def test_me_requires_auth(self, unauth_client: AsyncClient, mock_r2):
        resp = await unauth_client.get(f"{PREFIX}/me")
        assert resp.status_code == 401


class TestCreateExport:
    async def test_create_pending(self, client: AsyncClient, mock_r2):
        resp = await client.post(PREFIX)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["progress_pct"] == 0
        assert data["download_available"] is False
        assert data["filename"].endswith(".xlsx")
        UUID(data["id"])

    async def test_second_create_conflicts(self, client: AsyncClient, mock_r2):
        first = await client.post(PREFIX)
        assert first.status_code == 201
        second = await client.post(PREFIX)
        assert second.status_code == 409
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["status"] == "pending"

    async def test_create_after_ready_replaces(
        self, client: AsyncClient, db_session: AsyncSession, test_user, mock_r2
    ):
        first = await client.post(PREFIX)
        export_id = UUID(first.json()["id"])
        row = await db_session.get(DataExport, export_id)
        assert row is not None
        row.status = DataExportStatus.ready
        row.object_key = "exports/old/old.xlsx"
        row.completed_at = datetime.now(timezone.utc)
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        await db_session.commit()

        second = await client.post(PREFIX)
        assert second.status_code == 201
        assert second.json()["id"] != str(export_id)
        mock_r2["delete"].assert_awaited()


class TestGetExport:
    async def test_me_404_when_none(self, client: AsyncClient, mock_r2):
        resp = await client.get(f"{PREFIX}/me")
        assert resp.status_code == 404

    async def test_me_returns_latest(self, client: AsyncClient, mock_r2):
        created = await client.post(PREFIX)
        resp = await client.get(f"{PREFIX}/me")
        assert resp.status_code == 200
        assert resp.json()["id"] == created.json()["id"]
        assert "progress_pct" in resp.json()
        assert "progress_step" in resp.json()

    async def test_get_by_id(self, client: AsyncClient, mock_r2):
        created = await client.post(PREFIX)
        export_id = created.json()["id"]
        resp = await client.get(f"{PREFIX}/{export_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == export_id

    async def test_user_cannot_see_other_export(
        self,
        unauth_client: AsyncClient,
        db_session: AsyncSession,
        test_user,
        mock_r2,
    ):
        created_row = DataExport(
            owner_id=test_user.id,
            status=DataExportStatus.pending,
            filename="toston-export-test.xlsx",
        )
        db_session.add(created_row)
        await db_session.commit()
        await db_session.refresh(created_row)

        other = await create_test_user(db_session, email="other-export@example.com")
        headers = {"Authorization": f"Bearer {create_test_token(other)}"}
        resp = await unauth_client.get(f"{PREFIX}/{created_row.id}", headers=headers)
        assert resp.status_code == 404

        dl = await unauth_client.get(
            f"{PREFIX}/{created_row.id}/download", headers=headers
        )
        assert dl.status_code == 404


class TestDownloadExport:
    async def test_download_pending_conflicts(self, client: AsyncClient, mock_r2):
        created = await client.post(PREFIX)
        export_id = created.json()["id"]
        resp = await client.get(f"{PREFIX}/{export_id}/download")
        assert resp.status_code == 409

    async def test_download_ready_presigns_object_key(
        self, client: AsyncClient, db_session: AsyncSession, test_user, mock_r2
    ):
        created = await client.post(PREFIX)
        export_id = UUID(created.json()["id"])
        row = await db_session.get(DataExport, export_id)
        assert row is not None
        row.status = DataExportStatus.ready
        row.object_key = f"exports/{test_user.id}/{export_id}.xlsx"
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        await db_session.commit()

        resp = await client.get(f"{PREFIX}/{export_id}/download")
        assert resp.status_code == 200
        body = resp.json()
        assert body["url"] == "https://signed.example/file.xlsx"
        assert body["expires_in"] == 300
        mock_r2["presign"].assert_awaited()
        called_key = mock_r2["presign"].await_args.args[0]
        assert called_key == f"exports/{test_user.id}/{export_id}.xlsx"


class TestExportJobFailure:
    async def test_process_failure_marks_failed_and_allows_retry(
        self, client: AsyncClient, db_session: AsyncSession, test_user, mock_r2
    ):
        mock_r2["upload"].side_effect = RuntimeError("upload failed")
        created = await client.post(PREFIX)
        export_id = UUID(created.json()["id"])
        await process_export_job(db_session, export_id)
        me = await client.get(f"{PREFIX}/me")
        assert me.json()["status"] == "failed"
        assert me.json()["download_available"] is False

        retry = await client.post(PREFIX)
        assert retry.status_code == 201
        assert retry.json()["id"] != str(export_id)

    async def test_failed_allows_retry(
        self, client: AsyncClient, db_session: AsyncSession, test_user, mock_r2
    ):
        created = await client.post(PREFIX)
        export_id = UUID(created.json()["id"])
        row = await db_session.get(DataExport, export_id)
        assert row is not None
        row.status = DataExportStatus.failed
        row.error = "Export failed. Please try again."
        await db_session.commit()

        me = await client.get(f"{PREFIX}/me")
        assert me.json()["status"] == "failed"

        retry = await client.post(PREFIX)
        assert retry.status_code == 201
        assert retry.json()["id"] != str(export_id)


class TestProcessExport:
    async def test_process_marks_ready(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user,
        mock_r2,
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        await create_test_expense(
            db_session, owner_id=test_user.id, account_id=account.id
        )
        created = await client.post(PREFIX)
        export_id = UUID(created.json()["id"])
        await process_export_job(db_session, export_id)
        me = await client.get(f"{PREFIX}/me")
        assert me.status_code == 200
        assert me.json()["status"] == "ready"
        assert me.json()["download_available"] is True
        mock_r2["upload"].assert_awaited()
