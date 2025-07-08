from app.utilities.redis import r
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelMessage

MESSAGE_HISTORY_EXPIRE = 3600 * 24 # 1 day

def _history_key(user_id: int, session_id: str) -> str:
    return f"chat:history:{user_id}:{session_id}"

async def get_message_history(user_id: int, session_id: str) -> list[ModelMessage]:
    data = await r.get(_history_key(user_id, session_id))

    if data:
        return ModelMessagesTypeAdapter.validate_json(data)

    return []

async def store_message_history(user_id: int, session_id: str, messages: list[ModelMessage], expire_time: int = MESSAGE_HISTORY_EXPIRE):
    key = _history_key(user_id, session_id)
    await r.set(key, ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8"), ex=expire_time)