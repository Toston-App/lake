from types import SimpleNamespace

from app.services.clerk import _profile_from_user


def test_profile_uses_primary_email_and_full_name() -> None:
    clerk_user = SimpleNamespace(
        primary_email_address_id="email_primary",
        email_addresses=[
            SimpleNamespace(id="email_other", email_address="other@example.com"),
            SimpleNamespace(id="email_primary", email_address="me@example.com"),
        ],
        first_name="Ada",
        last_name="Lovelace",
        username="ada",
    )

    profile = _profile_from_user(clerk_user)

    assert profile.email == "me@example.com"
    assert profile.name == "Ada Lovelace"


def test_profile_falls_back_to_first_email_and_username() -> None:
    clerk_user = SimpleNamespace(
        primary_email_address_id=None,
        email_addresses=[SimpleNamespace(id="email_1", email_address="me@example.com")],
        first_name=None,
        last_name=None,
        username="ada",
    )

    profile = _profile_from_user(clerk_user)

    assert profile.email == "me@example.com"
    assert profile.name == "ada"
