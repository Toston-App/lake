"""CLI for dry-run-by-default Polar subscription reconciliation."""

from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import settings
from app.db.session import async_session
from app.services.polar import get_polar_client
from app.services.polar_reconciliation import reconcile_subscriptions


async def _run(*, repair: bool) -> None:
    if not settings.SUBSCRIPTIONS_ENABLED:
        raise SystemExit("subscriptions are disabled")
    async with async_session() as db:
        report = await reconcile_subscriptions(
            db,
            polar=get_polar_client(),
            repair=repair,
        )
    print(json.dumps(report.as_dict(), sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repair",
        action="store_true",
        help="apply detected subscription and cache repairs",
    )
    args = parser.parse_args()
    asyncio.run(_run(repair=args.repair))


if __name__ == "__main__":
    main()
