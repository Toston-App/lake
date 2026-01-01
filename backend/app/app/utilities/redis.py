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

# Cache categories and subcategories for 1 hour by default
async def cache_user_categories(user_id: int, categories_data, expire_time=3600):
    """Cache user categories and subcategories"""
    try:
        cache_key = f"user:{user_id}:categories"
        await r.setex(
            cache_key, 
            expire_time, 
            json.dumps(categories_data, cls=DateEncoder)
        )
        logging.info(f"Cached categories for user {user_id}")
        return True
    except Exception as e:
        logging.error(f"Error caching categories for user {user_id}: {str(e)}")
        return False

async def get_cached_user_categories(user_id: int):
    """Retrieve cached user categories and subcategories"""
    try:
        cache_key = f"user:{user_id}:categories"
        cached_data = await r.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        return None
    except (Exception, json.JSONDecodeError) as e:
        logging.error(f"Error retrieving cached categories for user {user_id}: {str(e)}")
        return None

async def invalidate_user_categories_cache(user_id: int):
    """Invalidate user categories cache when data changes"""
    try:
        cache_key = f"user:{user_id}:categories"
        await r.delete(cache_key)
        logging.info(f"Invalidated categories cache for user {user_id}")
        return True
    except Exception as e:
        logging.error(f"Error invalidating categories cache for user {user_id}: {str(e)}")
        return False

async def cache_category_lookup(category_ids: list[int], categories_data, expire_time=1800):
    """Cache category lookup data for batch operations"""
    try:
        cache_key = f"categories:batch:{':'.join(map(str, sorted(category_ids)))}"
        await r.setex(
            cache_key,
            expire_time,
            json.dumps(categories_data, cls=DateEncoder)
        )
        return True
    except Exception as e:
        logging.error(f"Error caching category batch lookup: {str(e)}")
        return False

async def get_cached_category_lookup(category_ids: list[int]):
    """Get cached category lookup data"""
    try:
        cache_key = f"categories:batch:{':'.join(map(str, sorted(category_ids)))}"
        cached_data = await r.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        return None
    except (Exception, json.JSONDecodeError) as e:
        logging.error(f"Error retrieving cached category batch lookup: {str(e)}")
        return None
