"""Production settings must fail closed around Polar subscriptions."""

import pytest
from app.core.config import Settings
from pydantic import ValidationError


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "ENVIRONMENT",
        "POLAR_SERVER",
        "SUBSCRIPTIONS_ENABLED",
        "SUBSCRIPTIONS_DEV_DEFAULT_TIER",
        "POLAR_ACCESS_TOKEN",
        "POLAR_WEBHOOK_SECRET",
        "POLAR_ORGANIZATION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_production_without_subscriptions_forces_free_tier(env) -> None:
    env.setenv("ENVIRONMENT", "production")
    env.setenv("SUBSCRIPTIONS_ENABLED", "false")
    env.setenv("SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier2")

    # POLAR_SERVER is left at its sandbox default.
    assert Settings().SUBSCRIPTIONS_DEV_DEFAULT_TIER == "free"


def test_unset_environment_is_treated_as_production(env) -> None:
    env.setenv("SUBSCRIPTIONS_ENABLED", "false")
    env.setenv("SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier2")

    settings = Settings()

    assert settings.is_production
    assert settings.SUBSCRIPTIONS_DEV_DEFAULT_TIER == "free"


def test_production_rejects_sandbox_polar(env) -> None:
    env.setenv("ENVIRONMENT", "production")
    env.setenv("POLAR_SERVER", "sandbox")
    env.setenv("POLAR_ACCESS_TOKEN", "token")
    env.setenv("POLAR_WEBHOOK_SECRET", "secret")
    env.setenv("POLAR_ORGANIZATION_ID", "org")

    with pytest.raises(ValidationError, match="POLAR_SERVER=production"):
        Settings()


def test_development_keeps_configured_dev_tier(env) -> None:
    env.setenv("ENVIRONMENT", "development")
    env.setenv("SUBSCRIPTIONS_ENABLED", "false")
    env.setenv("SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier2")

    assert Settings().SUBSCRIPTIONS_DEV_DEFAULT_TIER == "tier2"


def test_staging_may_use_sandbox_polar(env) -> None:
    env.setenv("ENVIRONMENT", "staging")
    env.setenv("POLAR_SERVER", "sandbox")
    env.setenv("POLAR_ACCESS_TOKEN", "token")
    env.setenv("POLAR_WEBHOOK_SECRET", "secret")
    env.setenv("POLAR_ORGANIZATION_ID", "org")

    assert Settings().SUBSCRIPTIONS_ENABLED is True
