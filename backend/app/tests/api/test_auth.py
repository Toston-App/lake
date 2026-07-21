"""
Tests for authentication endpoints and JWT token validation.

Covers:
- POST /api/v1/login/access-token (OAuth2 login)
- GET /api/v1/users/me (token validation)
- Local HS256 token acceptance/rejection
"""
from datetime import datetime, timedelta, timezone

from app.models.user import User
from httpx import AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_test_token
from tests.utils import create_test_user


def local_token_payload(user_id: int, *, expires_delta: timedelta) -> dict[str, object]:
    """Build claims matching ``schemas.LocalTokenPayload``."""
    now = datetime.now(timezone.utc)
    return {
        "sub": str(user_id),
        "iss": "local",
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": "test-jti",
    }


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

        # Local tokens contain only identity and standard validation claims;
        # user profile data is intentionally not embedded in the JWT.
        decoded = jwt.decode(
            body["access_token"],
            "foo",
            algorithms=["HS256"],
            issuer="local",
        )
        assert decoded["sub"] == str(user.id)
        assert decoded["iss"] == "local"
        assert {"iat", "exp", "jti"} <= decoded.keys()
        assert "user" not in decoded

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
# Protected endpoint: GET /api/v1/users/me
# ---------------------------------------------------------------------------
class TestProtectedEndpoint:
    """Test authentication through a registered protected endpoint."""

    async def test_valid_token_returns_user(
        self,
        unauth_client: AsyncClient,
        test_user: User,
        auth_headers: dict[str, str],
    ):
        """A valid Bearer token returns the current user's data."""
        response = await unauth_client.get(
            "/api/v1/users/me",
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body == {"country": test_user.country}

    async def test_no_token_returns_401(self, unauth_client: AsyncClient):
        """Request without Authorization header is rejected."""
        response = await unauth_client.get("/api/v1/users/me")

        assert response.status_code == 401

    async def test_invalid_token_returns_401(self, unauth_client: AsyncClient):
        """A completely invalid token string is rejected with 401."""
        response = await unauth_client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer not-a-valid-jwt"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Could not validate credentials"


# ---------------------------------------------------------------------------
# Local HS256 token validation (through the real auth dependency)
# ---------------------------------------------------------------------------
class TestHS256TokenValidation:
    """Test local tokens signed with LOCAL_JWT_SECRET."""

    async def test_valid_hs256_token_accepted(
        self,
        unauth_client: AsyncClient,
        test_user: User,
    ):
        """A properly formed local token is accepted."""
        token = create_test_token(test_user)
        response = await unauth_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json() == {"country": test_user.country}

    async def test_expired_token_rejected(
        self,
        unauth_client: AsyncClient,
        test_user: User,
    ):
        """An expired local token is rejected with 401."""
        expired_payload = local_token_payload(
            test_user.id, expires_delta=timedelta(hours=-1)
        )
        expired_token = jwt.encode(expired_payload, "foo", algorithm="HS256")

        response = await unauth_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Could not validate credentials"

    async def test_wrong_signing_key_rejected(
        self,
        unauth_client: AsyncClient,
        test_user: User,
    ):
        """A token signed with a different key is rejected."""
        payload = local_token_payload(test_user.id, expires_delta=timedelta(hours=1))
        bad_token = jwt.encode(payload, "wrong-key", algorithm="HS256")

        response = await unauth_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {bad_token}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Could not validate credentials"

    async def test_token_for_nonexistent_user(
        self, unauth_client: AsyncClient
    ):
        """A valid token referencing a non-existent user ID returns 404."""
        payload = local_token_payload(999999, expires_delta=timedelta(hours=1))
        token = jwt.encode(payload, "foo", algorithm="HS256")

        response = await unauth_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    async def test_malformed_payload_rejected(self, unauth_client: AsyncClient):
        """A token missing required local claims is rejected."""
        payload = {
            "iss": "local",
            "sub": "some-string",
        }
        token = jwt.encode(payload, "foo", algorithm="HS256")

        response = await unauth_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Could not validate credentials"


# ---------------------------------------------------------------------------
# Full login-then-use flow (integration)
# ---------------------------------------------------------------------------
class TestLoginFlow:
    """End-to-end: login, then use the token on a protected endpoint."""

    async def test_login_then_test_token(
        self, unauth_client: AsyncClient, db_session: AsyncSession
    ):
        """Login, receive a token, then retrieve the current user."""
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
        verify_resp = await unauth_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json() == {"country": user.country}
