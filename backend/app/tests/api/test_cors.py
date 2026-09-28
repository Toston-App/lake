"""CORS must never reflect arbitrary origins while credentials are allowed."""

import pytest
from app.core.config import Settings, settings
from httpx import AsyncClient
from pydantic import ValidationError


async def test_unknown_origin_is_not_reflected(unauth_client: AsyncClient) -> None:
    response = await unauth_client.get(
        "/api/v1/utils/health-check/",
        headers={"Origin": "https://evil.example", "Cookie": "__session=stolen"},
    )

    assert "access-control-allow-origin" not in response.headers


async def test_unknown_origin_preflight_is_rejected(unauth_client: AsyncClient) -> None:
    response = await unauth_client.options(
        "/api/v1/users/me",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_allow_listed_origin_is_reflected(unauth_client: AsyncClient) -> None:
    response = await unauth_client.get(
        "/api/v1/utils/health-check/",
        headers={"Origin": "https://cleverbill.ing"},
    )

    assert response.headers["access-control-allow-origin"] == "https://cleverbill.ing"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_wildcard_extra_cors_origin_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "https://ok.example,*")

    with pytest.raises(ValidationError, match="BACKEND_CORS_ORIGINS"):
        Settings()


def test_extra_cors_origins_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "https://a.example/, https://b.example")

    assert Settings().BACKEND_CORS_ORIGINS == [
        "https://a.example",
        "https://b.example",
    ]
    assert settings.BACKEND_CORS_ORIGINS == []
