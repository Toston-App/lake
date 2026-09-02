from uuid import uuid4

from app.models.user import User
from app.services.r2 import object_key


def test_object_key_uses_uuid_and_export_id():
    user = User(id=9, uuid=str(uuid4()), country="USD")
    export_id = uuid4()
    key = object_key(user, export_id)
    assert key == f"toston-user-exports/{user.uuid}/{export_id}.xlsx"


def test_object_key_falls_back_when_uuid_missing():
    user = User(id=42, uuid=None, country="USD")
    export_id = uuid4()
    key = object_key(user, export_id)
    assert key == f"toston-user-exports/user-42/{export_id}.xlsx"


def test_object_key_uses_configured_prefix(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "R2_PATH", "custom-folder/")
    user = User(id=1, uuid="abc", country="USD")
    export_id = uuid4()
    assert object_key(user, export_id) == f"custom-folder/abc/{export_id}.xlsx"


def test_presign_expiry_is_300(monkeypatch):
    captured: dict = {}

    class FakeClient:
        def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):
            captured["operation"] = operation
            captured["params"] = Params
            captured["expires"] = ExpiresIn
            return "https://example.invalid/file.xlsx"

    monkeypatch.setattr("app.services.r2._client", lambda: FakeClient())
    monkeypatch.setattr("app.services.r2.is_configured", lambda: True)

    from app.services.r2 import _presign_get_sync

    url = _presign_get_sync("exports/abc/file.xlsx", "toston-export-2026-08-31.xlsx", 300)
    assert url.startswith("https://")
    assert captured["expires"] == 300
    assert captured["operation"] == "get_object"
    assert "attachment" in captured["params"]["ResponseContentDisposition"]
