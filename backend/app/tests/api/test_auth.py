"""
Tests for authentication endpoints and JWT token validation.

Covers:
- POST /api/v1/login/access-token  (OAuth2 login)
- POST /api/v1/login/test-token    (token validation)
- HS256 dev token acceptance/rejection
"""
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.conftest import create_test_token
from tests.utils import create_test_user


# ---------------------------------------------------------------------------
# Login endpoint: POST /api/v1/login/access-token
# ---------------------------------------------------------------------------
class TestLoginAccessToken:
    """Test the OAuth2 login endpoint."""

    async def test_login_valid_credentials(
        self, unauth_client: AsyncClient, db_session: AsyncSession
    ):
        """Valid email + password returns an access token."""
        user = await create_test_user(
            db_session, email="login@example.com", password="secret123"
        )

        response = await unauth_client.post(
            "/api/v1/login/access-token",
            data={"username": "login@example.com", "password": "secret123"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

        # Verify the returned token is a valid HS256 JWT with expected payload
        decoded = jwt.decode(body["access_token"], "foo", algorithms=["HS256"])
        assert decoded["user"]["email"] == user.email
        assert decoded["user"]["id"] == user.id
        assert decoded["user"]["name"] == user.name
        assert "exp" in decoded

    async def test_login_wrong_password(
        self, unauth_client: AsyncClient, db_session: AsyncSession
    ):
        """Wrong password returns 400."""
        await create_test_user(
            db_session, email="wrongpw@example.com", password="correct"
        )

        response = await unauth_client.post(
            "/api/v1/login/access-token",
            data={"username": "wrongpw@example.com", "password": "incorrect"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Incorrect email or password"

    async def test_login_nonexistent_user(self, unauth_client: AsyncClient):
        """Email that doesn't exist returns 400."""
        response = await unauth_client.post(
            "/api/v1/login/access-token",
            data={"username": "nobody@example.com", "password": "anything"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Incorrect email or password"

    async def test_login_inactive_user(
        self, unauth_client: AsyncClient, db_session: AsyncSession
    ):
        """Inactive user returns 400 even with correct credentials."""
        await create_test_user(
            db_session,
            email="inactive@example.com",
            password="secret123",
            is_active=False,
        )

        response = await unauth_client.post(
            "/api/v1/login/access-token",
            data={"username": "inactive@example.com", "password": "secret123"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Inactive user"


# ---------------------------------------------------------------------------
# Test-token endpoint: POST /api/v1/login/test-token
# ---------------------------------------------------------------------------
class TestTestToken:
    """Test the token validation endpoint."""

    async def test_valid_token_returns_user(
        self,
        unauth_client: AsyncClient,
        test_user: User,
        auth_headers: dict[str, str],
    ):
        """A valid Bearer token returns the current user's data."""
        response = await unauth_client.post(
            "/api/v1/login/test-token",
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["email"] == test_user.email
        assert body["name"] == test_user.name
        assert body["id"] == test_user.id

    async def test_no_token_returns_401(self, unauth_client: AsyncClient):
        """Request without Authorization header is rejected."""
        response = await unauth_client.post("/api/v1/login/test-token")

        # OAuth2PasswordBearer returns 401 when no token is provided
        assert response.status_code == 401

    async def test_invalid_token_returns_403(self, unauth_client: AsyncClient):
        """A completely invalid token string is rejected with 403."""
        response = await unauth_client.post(
            "/api/v1/login/test-token",
            headers={"Authorization": "Bearer not-a-valid-jwt"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Could not validate credentials"


# ---------------------------------------------------------------------------
# HS256 dev token validation (through the real auth dependency)
# ---------------------------------------------------------------------------
class TestHS256TokenValidation:
    """Test that HS256 tokens signed with the 'foo' key are properly validated."""

    async def test_valid_hs256_token_accepted(
        self,
        unauth_client: AsyncClient,
        test_user: User,
    ):
        """A properly formed HS256 token with key 'foo' is accepted."""
        token = create_test_token(test_user)
        response = await unauth_client.post(
            "/api/v1/login/test-token",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["id"] == test_user.id

    async def test_expired_token_rejected(
        self,
        unauth_client: AsyncClient,
        test_user: User,
    ):
        """An expired HS256 token is rejected with 403."""
        expired_payload = {
            "exp": datetime.utcnow() - timedelta(hours=1),
            "user": {
                "name": test_user.name,
                "email": test_user.email,
                "country": test_user.country,
                "id": test_user.id,
            },
        }
        expired_token = jwt.encode(expired_payload, "foo", algorithm="HS256")

        response = await unauth_client.post(
            "/api/v1/login/test-token",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Could not validate credentials"

    async def test_wrong_signing_key_rejected(
        self,
        unauth_client: AsyncClient,
        test_user: User,
    ):
        """A token signed with a different key is rejected."""
        payload = {
            "exp": datetime.utcnow() + timedelta(hours=1),
            "user": {
                "name": test_user.name,
                "email": test_user.email,
                "country": test_user.country,
                "id": test_user.id,
            },
        }
        bad_token = jwt.encode(payload, "wrong-key", algorithm="HS256")

        response = await unauth_client.post(
            "/api/v1/login/test-token",
            headers={"Authorization": f"Bearer {bad_token}"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Could not validate credentials"

    async def test_token_for_nonexistent_user(
        self, unauth_client: AsyncClient
    ):
        """A valid token referencing a non-existent user ID returns 404."""
        payload = {
            "exp": datetime.utcnow() + timedelta(hours=1),
            "user": {
                "name": "Ghost",
                "email": "ghost@example.com",
                "country": "USD",
                "id": 999999,
            },
        }
        token = jwt.encode(payload, "foo", algorithm="HS256")

        response = await unauth_client.post(
            "/api/v1/login/test-token",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    async def test_malformed_payload_rejected(self, unauth_client: AsyncClient):
        """A token with missing 'user' dict is rejected."""
        payload = {
            "exp": datetime.utcnow() + timedelta(hours=1),
            "sub": "some-string",
        }
        token = jwt.encode(payload, "foo", algorithm="HS256")

        response = await unauth_client.post(
            "/api/v1/login/test-token",
            headers={"Authorization": f"Bearer {token}"},
        )

        # The dep tries payload.get("user", {}).get("email") → None (falsy),
        # then TokenPayloadUuid(**payload) fails with ValidationError (missing
        # required fields), which is caught → 403.
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Full login-then-use flow (integration)
# ---------------------------------------------------------------------------
class TestLoginFlow:
    """End-to-end: login, get token, use token to access protected endpoint."""

    async def test_login_then_test_token(
        self, unauth_client: AsyncClient, db_session: AsyncSession
    ):
        """Login, receive a token, then use it on the test-token endpoint."""
        user = await create_test_user(
            db_session, email="flow@example.com", password="flowpass"
        )

        # Step 1: login
        login_resp = await unauth_client.post(
            "/api/v1/login/access-token",
            data={"username": "flow@example.com", "password": "flowpass"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        # Step 2: use the token
        verify_resp = await unauth_client.post(
            "/api/v1/login/test-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["email"] == user.email
        assert verify_resp.json()["id"] == user.id
