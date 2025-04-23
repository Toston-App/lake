import datetime
import json

from upstash_redis.asyncio import Redis

from app.core.config import settings
from app.utilities.logger import setup_logger

logging = setup_logger("redis", "redis.log")
r = Redis(url=settings.REDIS_URL, token=settings.REDIS_TOKEN)

class DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        return super().default(obj)

# 30 minutes expiration time by default
async def store_transaction(transaction_id:str, transaction_data, user_id, expire_time=1800):
    """Store transaction data in Redis with expiration time"""
    try:
        # Store the data as hash
        await r.hmset(transaction_id, {
            "data": json.dumps(transaction_data, cls=DateEncoder),
            "user_id": user_id
        })

        await r.expire(transaction_id, expire_time)

        return True
    except Redis.RedisError as e:
        logging.error(f"Redis error storing transaction {transaction_id}: {str(e)}")
        return False

async def get_transaction(transaction_id: str):
    """Retrieve transaction data from Redis with proper decoding"""
    try:
        cached_data = await r.hgetall(transaction_id)
        if not cached_data:
            return None

        # Decode binary data to strings
        decoded_data = {k.decode('utf-8'): v.decode('utf-8') for k, v in cached_data.items()}

        # Parse JSON string back to dict
        if "data" in decoded_data:
            decoded_data["data"] = json.loads(decoded_data["data"])

        return decoded_data
    except (Redis.RedisError, json.JSONDecodeError) as e:
        logging.error(f"Error retrieving transaction {transaction_id}: {str(e)}")
        return None

async def delete_transaction(transaction_id: str):
    """Delete transaction data from Redis"""
    try:
        await r.delete(transaction_id)

        return True
    except Redis.RedisError as e:
        logging.error(f"Error deleting transaction {transaction_id}: {str(e)}")
        return False
