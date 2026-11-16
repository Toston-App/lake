"""The user's display currency and default account, as the MCP tools see them.

``user.country`` holds a BCP 47 locale (``es-MX``) from which the app derives
the currency; older accounts hold the currency code itself (``MXN``). The
mapping follows the web app's maps in wallet_v2 ``providers/Currency.tsx``
(``DEFAULT_CURRENCY_LOCALE_MAP`` and its reverse): keep them in sync.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.services.errors import DomainError
from app.utilities.redis import invalidate_user_cache

CURRENCY_LOCALES: dict[str, str] = {
    # "ar-AR" would format amounts right to left; the web picker uses es-AR.
    "ARS": "es-AR",
    "AUD": "en-AU",
    "BRL": "pt-BR",
    "CAD": "en-CA",
    "CHF": "de-CH",
    "CNY": "zh-CN",
    "COP": "es-CO",
    "DKK": "da-DK",
    "EUR": "de-DE",
    "GBP": "en-GB",
    "HKD": "zh-HK",
    "INR": "en-IN",
    "JPY": "ja-JP",
    "KRW": "ko-KR",
    "MXN": "es-MX",
    "NOK": "nb-NO",
    "NZD": "en-NZ",
    "PLN": "pl-PL",
    "RUB": "ru-RU",
    "SEK": "sv-SE",
    "SGD": "en-SG",
    "USD": "en-US",
    "ZAR": "en-ZA",
}
_LOCALE_CURRENCIES = {locale: code for code, locale in CURRENCY_LOCALES.items()}
# Older web app versions wrote this spelling for ARS.
_LOCALE_CURRENCIES["ar-AR"] = "ARS"

MAX_NAME_LENGTH = 100


def currency_of(user: models.User) -> str | None:
    """The user's display currency, or None if their locale names none."""
    country = getattr(user, "country", None) or ""
    if country in CURRENCY_LOCALES:
        return country
    return _LOCALE_CURRENCIES.get(country)


# Currencies of the Spanish-speaking markets the app serves, for older
# accounts that store the currency code instead of a locale.
_SPANISH_CURRENCIES = frozenset({"MXN", "ARS", "COP"})


def language_of(user: models.User) -> str:
    """``es`` or ``en``: the language the user's locale (or currency) implies."""
    locale = getattr(user, "country", None) or ""
    if locale.lower().startswith("es"):
        return "es"
    return "es" if currency_of(user) in _SPANISH_CURRENCIES else "en"


def profile_of(user: models.User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "locale": user.country,
        "currency": currency_of(user),
        "default_account_id": user.default_account_id,
    }


async def update_profile(
    db: AsyncSession,
    user: models.User,
    *,
    name: str | None = None,
    currency: str | None = None,
    default_account_id: int | None = None,
) -> models.User:
    """Change the name, display currency or default account.

    The currency only changes how amounts are shown: stored amounts are not
    converted. Email, password and phone are never changed here.
    """
    changes: dict = {}
    if name is not None:
        name = name.strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            raise DomainError(f"name must be 1 to {MAX_NAME_LENGTH} characters")
        changes["name"] = name
    if currency is not None:
        locale = CURRENCY_LOCALES.get(currency.upper())
        if locale is None:
            raise DomainError(
                "Unsupported currency. Use one of: " + ", ".join(CURRENCY_LOCALES)
            )
        changes["country"] = locale
    if default_account_id is not None:
        account = await crud.account.get_by_id(
            db, owner_id=user.id, id=default_account_id
        )
        if account is None:
            raise DomainError("Account not found")
        changes["default_account_id"] = default_account_id
    if not changes:
        raise DomainError(
            "Nothing to change: pass name, currency or default_account_id"
        )
    for key, value in changes.items():
        setattr(user, key, value)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if "country" in changes:
        # Cached period summaries carry the locale.
        await invalidate_user_cache(user.id)
    return user
