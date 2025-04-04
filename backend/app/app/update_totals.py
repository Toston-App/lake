import asyncio
import logging

from app.db.init_totals import update_category_totals, update_subcategory_totals
from app.db.session import async_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def init() -> None:
    async with async_session() as db:
        # Update category totals
        await update_category_totals(db)

        # Update subcategory totals
        await update_subcategory_totals(db)


async def main() -> None:
    logger.info("Updating category and subcategory totals")
    await init()
    logger.info("Totals updated successfully")


if __name__ == "__main__":
    asyncio.run(main())
