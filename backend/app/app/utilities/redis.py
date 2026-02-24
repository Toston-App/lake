import datetime
import json

from upstash_redis.asyncio import Redis

from app.core.config import settings
from app.utilities.logger import setup_logger

logging = setup_logger("redis", "redis.log")
r = Redis(url=settings.REDIS_URL, token=settings.REDIS_TOKEN, allow_telemetry=False)


class DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        return super().default(obj)


# 30 minutes expiration time by default
async def store_transaction(
    transaction_id: str, transaction_data, user_id, expire_time=1800
):
    """Store transaction data in Redis with expiration time"""
    try:
        # Store the data as hash
        await r.hmset(
            transaction_id,
            {"data": json.dumps(transaction_data, cls=DateEncoder), "user_id": user_id},
        )

        await r.expire(transaction_id, expire_time)

        return True
    except Exception as e:
        logging.error(f"Redis error storing transaction {transaction_id}: {str(e)}")
        return False


async def get_transaction(transaction_id: str):
    """Retrieve transaction data from Redis with proper decoding"""
    try:
        cached_data = await r.hgetall(transaction_id)
        if not cached_data:
            return None

        # Parse JSON string back to dict
        if "data" in cached_data:
            cached_data["data"] = json.loads(cached_data["data"])

        return cached_data
    except (Exception, json.JSONDecodeError) as e:
        logging.error(f"Error retrieving transaction {transaction_id}: {str(e)}")
        return None


async def delete_transaction(transaction_id: str):
    """Delete transaction data from Redis"""
    try:
        await r.delete(transaction_id)

        return True
    except Exception as e:
        logging.error(f"Error deleting transaction {transaction_id}: {str(e)}")
        return False


# ==================== Recap Caching Functions ====================


def _get_recap_key(user_id: int, year: int) -> str:
    """Generate Redis key for user recap"""
    return f"recap:{user_id}:{year}"


def _get_recap_status_key(user_id: int, year: int) -> str:
    """Generate Redis key for recap generation status"""
    return f"recap_status:{user_id}:{year}"


async def get_recap(user_id: int, year: int) -> dict | None:
    """Retrieve recap data from Redis"""
    try:
        key = _get_recap_key(user_id, year)
        cached_data = await r.get(key)

        if not cached_data:
            return None

        return json.loads(cached_data)
    except (Exception, json.JSONDecodeError) as e:
        logging.error(
            f"Error retrieving recap for user {user_id}, year {year}: {str(e)}"
        )
        return None


async def get_recap_status(user_id: int, year: int) -> dict | None:
    """Retrieve recap generation status from Redis"""
    try:
        key = _get_recap_status_key(user_id, year)
        cached_data = await r.get(key)

        if not cached_data:
            return None

        return json.loads(cached_data)
    except (Exception, json.JSONDecodeError) as e:
        logging.error(
            f"Error retrieving recap status for user {user_id}, year {year}: {str(e)}"
        )
        return None
