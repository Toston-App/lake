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

# User data caching functionality
async def store_user_data(user_id: int, date_filter_type: str, date: str, data, expire_time=1800):
    """Store get_all_data response in Redis with expiration time (30 min default)"""
    try:
        cache_key = f"user_data:{user_id}:{date_filter_type}:{date}"
        
        await r.set(cache_key, json.dumps(data, cls=DateEncoder))
        await r.expire(cache_key, expire_time)
        
        logging.info(f"Cached user data for user {user_id} with key {cache_key}")
        return True
    except Exception as e:
        logging.error(f"Redis error storing user data for user {user_id}: {str(e)}")
        return False

async def get_user_data(user_id: int, date_filter_type: str, date: str):
    """Retrieve cached get_all_data response from Redis"""
    try:
        cache_key = f"user_data:{user_id}:{date_filter_type}:{date}"
        cached_data = await r.get(cache_key)
        
        if cached_data:
            logging.info(f"Cache hit for user {user_id} with key {cache_key}")
            return json.loads(cached_data)
        
        logging.info(f"Cache miss for user {user_id} with key {cache_key}")
        return None
    except (Exception, json.JSONDecodeError) as e:
        logging.error(f"Error retrieving user data for user {user_id}: {str(e)}")
        return None

async def invalidate_user_cache(user_id: int):
    """Invalidate all cached data for a specific user when they add/update/delete data"""
    try:
        # Get all keys that match the user's cache pattern
        pattern = f"user_data:{user_id}:*"
        keys = await r.keys(pattern)
        
        if keys:
            await r.delete(*keys)
            logging.info(f"Invalidated {len(keys)} cache entries for user {user_id}")
        
        return True
    except Exception as e:
        logging.error(f"Error invalidating cache for user {user_id}: {str(e)}")
        return False
