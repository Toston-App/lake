"""Operator-safe reconciliation of local subscription state with Polar."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tiers import Tier, tier_for_product
from app.models.subscription import UserSubscription
from app.services.entitlements import entitlement_payload
from app.services.polar import _configure_product_ids
from app.utilities.redis import get_entitlement, set_entitlement


@dataclass
class ReconciliationReport:
    scanned: int = 0
    drifted_user_ids: list[int] = field(default_factory=list)
    unresolved_user_ids: list[int] = field(default_factory=list)
    unknown_product_subscription_ids: list[str] = field(default_factory=list)
    stale_cache_user_ids: list[int] = field(default_factory=list)
    repaired_user_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["counts"] = {
            "scanned": self.scanned,
            "drifted": len(self.drifted_user_ids),
            "unresolved": len(self.unresolved_user_ids),
            "unknown_products": len(self.unknown_product_subscription_ids),
            "stale_cache": len(self.stale_cache_user_ids),
            "repaired": len(self.repaired_user_ids),
        }
        return result


def _status_value(value: Any) -> str:
    return getattr(value, "value", str(value))


def _remote_fields(remote: Any) -> dict[str, Any]:
    return {
        "polar_customer_id": getattr(remote, "customer_id", None),
        "polar_subscription_id": getattr(remote, "id", None),
        "product_id": getattr(remote, "product_id", None),
        "status": _status_value(getattr(remote, "status", "unknown")),
        "current_period_start": getattr(remote, "current_period_start", None),
        "current_period_end": getattr(remote, "current_period_end", None),
        "cancel_at_period_end": bool(getattr(remote, "cancel_at_period_end", False)),
        "canceled_at": getattr(remote, "canceled_at", None),
        "ends_at": getattr(remote, "ends_at", None),
        "ended_at": getattr(remote, "ended_at", None),
    }


def _row_differs(row: UserSubscription, fields: dict[str, Any], tier: Tier) -> bool:
    compared = (
        "polar_customer_id",
        "polar_subscription_id",
        "status",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "canceled_at",
        "ends_at",
        "ended_at",
    )
    return row.tier != tier.value or any(
        getattr(row, name) != fields[name] for name in compared
    )


def _apply_remote(row: UserSubscription, fields: dict[str, Any], tier: Tier) -> None:
    row.tier = tier.value
    for name, value in fields.items():
        if name != "product_id":
            setattr(row, name, value)
    row.last_event_at = datetime.now(timezone.utc)
    row.last_event_priority = 0
    row.polar_metadata = {"source": "reconciliation"}


async def reconcile_subscriptions(
    db: AsyncSession,
    *,
    polar: Any,
    repair: bool = False,
) -> ReconciliationReport:
    """Compare local rows with Polar; mutate only with explicit ``repair``.

    The report contains identifiers and counts, never customer payloads.
    """
    # Without the env product ids every subscription maps to free and would be
    # reported as an unknown product, silently skipping all repairs.
    _configure_product_ids()
    rows = (await db.execute(select(UserSubscription))).scalars().all()
    report = ReconciliationReport(scanned=len(rows))
    repaired_rows: list[UserSubscription] = []

    from polar_sdk.models import ResourceNotFound

    for row in rows:
        if not row.polar_subscription_id:
            report.unresolved_user_ids.append(row.user_id)
            continue
        try:
            remote = await polar.subscriptions.get_async(id=row.polar_subscription_id)
        except ResourceNotFound:
            report.unresolved_user_ids.append(row.user_id)
            continue

        fields = _remote_fields(remote)
        tier = tier_for_product(fields["product_id"])
        if tier == Tier.free:
            report.unknown_product_subscription_ids.append(row.polar_subscription_id)
            continue

        if _row_differs(row, fields, tier):
            report.drifted_user_ids.append(row.user_id)
            if repair:
                _apply_remote(row, fields, tier)
                repaired_rows.append(row)

        expected = entitlement_payload(row)
        cached = await get_entitlement(row.user_id)
        if cached is not None and cached != expected:
            report.stale_cache_user_ids.append(row.user_id)

    if not repair:
        return report

    await db.commit()
    repair_ids = set(report.stale_cache_user_ids)
    repair_ids.update(row.user_id for row in repaired_rows)
    rows_by_user = {row.user_id: row for row in rows}
    for user_id in sorted(repair_ids):
        if not await set_entitlement(
            user_id, entitlement_payload(rows_by_user[user_id])
        ):
            raise RuntimeError(f"failed to repair entitlement cache for user {user_id}")
        report.repaired_user_ids.append(user_id)
    return report
