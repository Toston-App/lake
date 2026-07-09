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


# ==================== Dashboard Cache Functions ====================

# Removed `"comparison"` since this is not used for v3 migration but could be helpful for future features.
CACHE_PREFIXES = ["charts", "summary"]
CACHE_TTL = 60 * 60 * 24 * 7  # 7 days in seconds


async def store_cached(
    prefix: str, user_id: int, filter_type: str, date_param: str, data: dict
) -> bool:
    """Store computed dashboard data in Redis with 7-day TTL."""
    try:
        key = f"{prefix}:{user_id}:{filter_type}:{date_param}"
        await r.set(key, json.dumps(data, cls=DateEncoder))
        await r.expire(key, CACHE_TTL)
        return True
    except Exception as e:
        logging.error(f"Redis error storing cache {key}: {str(e)}")
        return False


async def get_cached(
    prefix: str, user_id: int, filter_type: str, date_param: str
) -> dict | None:
    """Retrieve cached dashboard data from Redis."""
    try:
        key = f"{prefix}:{user_id}:{filter_type}:{date_param}"
        cached = await r.get(key)
        if cached:
            return json.loads(cached)
        return None
    except (Exception, json.JSONDecodeError) as e:
        logging.error(f"Redis error retrieving cache {key}: {str(e)}")
        return None


# ==================== Chat conversation storage ====================

CHAT_CONVERSATION_PREFIX = "chat:conv"
CHAT_USER_INDEX_PREFIX = "chat:user"
CHAT_PENDING_PREFIX = "chat:pending"
CHAT_PENDING_TTL = 1800


def _chat_conversation_key(conversation_id: str) -> str:
    return f"{CHAT_CONVERSATION_PREFIX}:{conversation_id}"


def _chat_user_index_key(user_id: int) -> str:
    return f"{CHAT_USER_INDEX_PREFIX}:{user_id}"


def _chat_pending_key(action_id: str) -> str:
    return f"{CHAT_PENDING_PREFIX}:{action_id}"


def _message_preview(msg: dict) -> str:
    """Best-effort text preview for the sidebar.

    Handles both legacy messages (``{role, content}``) and Vercel AI UI
    messages (``{id, role, parts}``) saved by the Pydantic AI flow.
    """
    content = msg.get("content")
    if isinstance(content, str) and content:
        return content
    parts = msg.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


async def get_conversation(conversation_id: str, user_id: int) -> dict | None:
    """Load a conversation if it belongs to the user."""
    try:
        key = _chat_conversation_key(conversation_id)
        raw = await r.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("user_id") != user_id:
            return None
        return data
    except (Exception, json.JSONDecodeError) as e:
        logging.error(f"Error loading conversation {conversation_id}: {e}")
        return None


async def save_conversation(
    conversation_id: str,
    user_id: int,
    messages: list[dict],
    title: str,
    ttl: int,
) -> bool:
    """Persist conversation messages and update the user's conversation index."""
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = {
            "id": conversation_id,
            "user_id": user_id,
            "title": title,
            "messages": messages,
            "updated_at": now,
        }
        conv_key = _chat_conversation_key(conversation_id)
        await r.set(conv_key, json.dumps(payload, cls=DateEncoder))
        await r.expire(conv_key, ttl)

        index_key = _chat_user_index_key(user_id)
        await r.hset(index_key, conversation_id, json.dumps({"title": title, "updated_at": now}))
        await r.expire(index_key, ttl)
        return True
    except Exception as e:
        logging.error(f"Error saving conversation {conversation_id}: {e}")
        return False


async def list_user_conversations(user_id: int) -> list[dict]:
    """List conversation summaries for a user."""
    try:
        index_key = _chat_user_index_key(user_id)
        entries = await r.hgetall(index_key)
        if not entries:
            return []
        result = []
        for conv_id, meta_raw in entries.items():
            meta = json.loads(meta_raw)
            conv = await get_conversation(conv_id, user_id)
            preview = None
            if conv and conv.get("messages"):
                last = conv["messages"][-1]
                preview = _message_preview(last)[:120]
            result.append(
                {
                    "id": conv_id,
                    "title": meta.get("title") or "Conversation",
                    "updated_at": meta.get("updated_at"),
                    "last_message_preview": preview,
                }
            )
        result.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return result
    except Exception as e:
        logging.error(f"Error listing conversations for user {user_id}: {e}")
        return []


async def delete_conversation(conversation_id: str, user_id: int) -> bool:
    try:
        conv = await get_conversation(conversation_id, user_id)
        if not conv:
            return False
        await r.delete(_chat_conversation_key(conversation_id))
        await r.hdel(_chat_user_index_key(user_id), conversation_id)
        return True
    except Exception as e:
        logging.error(f"Error deleting conversation {conversation_id}: {e}")
        return False


async def store_pending_action(
    action_id: str,
    user_id: int,
    conversation_id: str,
    tool_name: str,
    tool_args: dict,
    preview: dict,
    expire_time: int = CHAT_PENDING_TTL,
    turn_id: int | None = None,
) -> bool:
    try:
        payload = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "preview": preview,
            "turn_id": turn_id,
        }
        key = _chat_pending_key(action_id)
        await r.set(key, json.dumps(payload, cls=DateEncoder))
        await r.expire(key, expire_time)
        return True
    except Exception as e:
        logging.error(f"Error storing pending action {action_id}: {e}")
        return False


async def get_pending_action(action_id: str, user_id: int) -> dict | None:
    try:
        raw = await r.get(_chat_pending_key(action_id))
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("user_id") != user_id:
            return None
        return data
    except (Exception, json.JSONDecodeError) as e:
        logging.error(f"Error loading pending action {action_id}: {e}")
        return None


async def delete_pending_action(action_id: str) -> bool:
    try:
        await r.delete(_chat_pending_key(action_id))
        return True
    except Exception as e:
        logging.error(f"Error deleting pending action {action_id}: {e}")
        return False


async def invalidate_user_cache(user_id: int) -> bool:
    """Delete all cached dashboard data for a user using SCAN + DEL."""
    try:
        for prefix in CACHE_PREFIXES:
            pattern = f"{prefix}:{user_id}:*"
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                if keys:
                    await r.delete(*keys)
                if cursor == 0:
                    break
        # Also drop the entitlement cache so a fresh webhook is reflected
        # immediately on the next chat turn.
        await r.delete(_entitlement_key(user_id))
        return True
    except Exception as e:
        logging.error(
            f"Redis error invalidating cache for user {user_id}: {str(e)}"
        )
        return False


# ==================== Subscription entitlement cache ====================

ENTITLEMENT_PREFIX = "chat:ent"
ENTITLEMENT_TTL = 3600  # 1 hour


def _entitlement_key(user_id: int) -> str:
    return f"{ENTITLEMENT_PREFIX}:{user_id}"


async def get_entitlement(user_id: int) -> dict | None:
    """Read the cached entitlement payload (tier, status, period window)."""
    try:
        cached = await r.get(_entitlement_key(user_id))
        if cached:
            return json.loads(cached)
        return None
    except Exception as e:
        logging.error(f"Redis error reading entitlement {user_id}: {e}")
        return None


async def set_entitlement(user_id: int, payload: dict) -> bool:
    try:
        await r.set(_entitlement_key(user_id), json.dumps(payload, default=str))
        await r.expire(_entitlement_key(user_id), ENTITLEMENT_TTL)
        return True
    except Exception as e:
        logging.error(f"Redis error writing entitlement {user_id}: {e}")
        return False


async def invalidate_entitlement(user_id: int) -> bool:
    try:
        await r.delete(_entitlement_key(user_id))
        return True
    except Exception as e:
        logging.error(f"Redis error deleting entitlement {user_id}: {e}")
        return False
