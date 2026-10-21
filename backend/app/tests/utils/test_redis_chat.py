import asyncio
import json

import pytest
from app.utilities import redis


@pytest.fixture(scope="session", autouse=True)
def setup_database() -> None:
    """Redis unit tests do not require the integration database."""


class AtomicRedisFake:
    """Small Redis fake that serializes the exact Lua/NX primitives in use."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires_at: dict[str, float] = {}
        self.now = 0.0
        self.mutex = asyncio.Lock()
        self.fail_eval = False
        self.fail_set = False

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _purge(self, key: str) -> None:
        deadline = self.expires_at.get(key)
        if deadline is not None and deadline <= self.now:
            self.values.pop(key, None)
            self.hashes.pop(key, None)
            self.expires_at.pop(key, None)

    async def get(self, key: str) -> str | None:
        async with self.mutex:
            self._purge(key)
            return self.values.get(key)

    async def ttl(self, key: str) -> int:
        async with self.mutex:
            self._purge(key)
            if key not in self.values and key not in self.hashes:
                return -2
            if key not in self.expires_at:
                return -1
            return max(0, int(self.expires_at[key] - self.now))

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool | None = None,
        ex: int | None = None,
    ) -> str | None:
        async with self.mutex:
            if self.fail_set:
                raise RuntimeError("injected set failure")
            self._purge(key)
            if nx and key in self.values:
                return None
            self.values[key] = value
            if ex is not None:
                self.expires_at[key] = self.now + ex
            return "OK"

    async def hgetall(self, key: str) -> dict[str, str]:
        async with self.mutex:
            self._purge(key)
            return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        async with self.mutex:
            stored = self.hashes.setdefault(key, {})
            deleted = 0
            for field in fields:
                deleted += int(field in stored)
                stored.pop(field, None)
            return deleted

    async def expire(self, key: str, seconds: int) -> int:
        async with self.mutex:
            self._purge(key)
            if key not in self.values and key not in self.hashes:
                return 0
            self.expires_at[key] = self.now + int(seconds)
            return 1

    async def eval(
        self, script: str, *, keys: list[str], args: list[str]
    ) -> int:
        async with self.mutex:
            if self.fail_eval:
                raise RuntimeError("injected eval failure")
            for key in keys:
                self._purge(key)

            if script == redis._SAVE_CONVERSATION_SCRIPT:
                self.values[keys[0]] = args[0]
                self.expires_at[keys[0]] = self.now + int(args[1])
                self.hashes.setdefault(keys[1], {})[args[2]] = args[3]
                self.expires_at[keys[1]] = self.now + int(args[1])
                return 1
            if script == redis._DELETE_CONVERSATION_SCRIPT:
                deleted = int(keys[0] in self.values)
                self.values.pop(keys[0], None)
                self.expires_at.pop(keys[0], None)
                self.hashes.setdefault(keys[1], {}).pop(args[0], None)
                return deleted
            if script == redis._MIGRATE_CONVERSATION_SCRIPT:
                if self.values.get(keys[1]) != args[0]:
                    return 0
                if keys[0] not in self.values:
                    self.values[keys[0]] = args[0]
                    self.expires_at[keys[0]] = self.now + int(args[1])
                    self.hashes.setdefault(keys[2], {})[args[2]] = args[3]
                    self.expires_at[keys[2]] = self.now + int(args[1])
                self.values.pop(keys[1], None)
                self.expires_at.pop(keys[1], None)
                return 1
            if script == redis._RELEASE_LOCK_SCRIPT:
                if self.values.get(keys[0]) != args[0]:
                    return 0
                self.values.pop(keys[0], None)
                self.expires_at.pop(keys[0], None)
                return 1
            if script == redis._REFRESH_LOCK_SCRIPT:
                if self.values.get(keys[0]) != args[0]:
                    return 0
                self.expires_at[keys[0]] = self.now + int(args[1])
                return 1
            raise AssertionError("unexpected Lua script")


@pytest.fixture
def atomic_redis(monkeypatch: pytest.MonkeyPatch) -> AtomicRedisFake:
    fake = AtomicRedisFake()
    monkeypatch.setattr(redis, "r", fake)
    return fake


@pytest.mark.asyncio
async def test_same_conversation_id_is_isolated_by_user(
    atomic_redis: AtomicRedisFake,
) -> None:
    assert await redis.save_conversation("same-id", 1, [{"content": "one"}], "One", 60)
    assert await redis.save_conversation("same-id", 2, [{"content": "two"}], "Two", 60)

    first = await redis.get_conversation("same-id", 1)
    second = await redis.get_conversation("same-id", 2)

    assert first and first["messages"] == [{"content": "one"}]
    assert second and second["messages"] == [{"content": "two"}]
    assert set(atomic_redis.values) == {"chat:conv:1:same-id", "chat:conv:2:same-id"}


@pytest.mark.asyncio
async def test_save_and_delete_update_blob_and_index_atomically(
    atomic_redis: AtomicRedisFake,
) -> None:
    assert atomic_redis is not None
    messages = [{"parts": [{"type": "text", "text": "Latest response"}]}]
    assert await redis.save_conversation("conversation-1", 42, messages, "Budget", 60)

    listed = await redis.list_user_conversations(42)
    assert listed[0]["last_message_preview"] == "Latest response"
    assert await redis.delete_conversation("conversation-1", 42)
    assert await redis.get_conversation("conversation-1", 42) is None
    assert await redis.list_user_conversations(42) == []


@pytest.mark.asyncio
async def test_list_prunes_expired_index_entries(
    atomic_redis: AtomicRedisFake,
) -> None:
    atomic_redis.hashes["chat:user:42"] = {
        "expired": json.dumps(
            {
                "title": "Old",
                "updated_at": "2020-01-01T00:00:00+00:00",
                "expires_at": "2020-01-01T00:01:00+00:00",
            }
        )
    }

    assert await redis.list_user_conversations(42) == []
    assert await atomic_redis.hgetall("chat:user:42") == {}


@pytest.mark.asyncio
async def test_legacy_value_migrates_only_for_embedded_owner(
    atomic_redis: AtomicRedisFake,
) -> None:
    payload = json.dumps(
        {
            "id": "legacy-id",
            "user_id": 7,
            "title": "Legacy",
            "messages": [{"content": "owned"}],
            "updated_at": "2026-08-05T00:00:00+00:00",
        }
    )
    await atomic_redis.set("chat:conv:legacy-id", payload, ex=90)

    assert await redis.get_conversation("legacy-id", 8) is None
    assert await atomic_redis.get("chat:conv:legacy-id") == payload

    migrated = await redis.get_conversation("legacy-id", 7)
    assert migrated and migrated["user_id"] == 7
    assert await atomic_redis.get("chat:conv:legacy-id") is None
    assert await atomic_redis.get("chat:conv:7:legacy-id") == payload
    assert (await atomic_redis.hgetall("chat:user:7"))["legacy-id"]


@pytest.mark.asyncio
async def test_legacy_migration_does_not_overwrite_a_concurrent_owned_save(
    atomic_redis: AtomicRedisFake,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = json.dumps(
        {"id": "race", "user_id": 7, "title": "Legacy", "messages": []}
    )
    newer = json.dumps(
        {"id": "race", "user_id": 7, "title": "Newer", "messages": []}
    )
    await atomic_redis.set("chat:conv:race", legacy, ex=90)
    original_ttl = atomic_redis.ttl

    async def concurrent_save_before_migration(key: str) -> int:
        atomic_redis.values["chat:conv:7:race"] = newer
        atomic_redis.hashes["chat:user:7"] = {
            "race": json.dumps({"title": "Newer"})
        }
        return await original_ttl(key)

    monkeypatch.setattr(atomic_redis, "ttl", concurrent_save_before_migration)

    loaded = await redis.get_conversation("race", 7)
    assert loaded and loaded["title"] == "Newer"
    meta = json.loads((await atomic_redis.hgetall("chat:user:7"))["race"])
    assert meta["title"] == "Newer"


@pytest.mark.asyncio
async def test_lock_rejects_concurrent_same_owner_but_not_other_users(
    atomic_redis: AtomicRedisFake,
) -> None:
    assert atomic_redis is not None
    first = await redis.acquire_conversation_lock(1, "shared", ttl=10)
    assert first is not None
    assert await redis.acquire_conversation_lock(1, "shared", ttl=10) is None
    assert await redis.acquire_conversation_lock(2, "shared", ttl=10) is not None

    assert await redis.release_conversation_lock(1, "shared", "wrong-token") is False
    assert await redis.acquire_conversation_lock(1, "shared", ttl=10) is None
    assert await redis.release_conversation_lock(1, "shared", first) is True
    assert await redis.acquire_conversation_lock(1, "shared", ttl=10) is not None


@pytest.mark.asyncio
async def test_simultaneous_lock_attempts_have_exactly_one_winner(
    atomic_redis: AtomicRedisFake,
) -> None:
    assert atomic_redis is not None
    attempts = await asyncio.gather(
        redis.acquire_conversation_lock(3, "simultaneous", ttl=10),
        redis.acquire_conversation_lock(3, "simultaneous", ttl=10),
    )

    assert sum(token is not None for token in attempts) == 1


@pytest.mark.asyncio
async def test_expired_lock_can_be_reacquired_without_old_owner_deleting_new_lock(
    atomic_redis: AtomicRedisFake,
) -> None:
    expired_token = await redis.acquire_conversation_lock(1, "expiry", ttl=5)
    assert expired_token
    atomic_redis.advance(6)
    new_token = await redis.acquire_conversation_lock(1, "expiry", ttl=5)
    assert new_token and new_token != expired_token

    assert await redis.release_conversation_lock(1, "expiry", expired_token) is False
    assert await redis.refresh_conversation_lock(1, "expiry", new_token, ttl=10)


@pytest.mark.asyncio
async def test_write_failures_are_explicit(
    atomic_redis: AtomicRedisFake,
) -> None:
    atomic_redis.fail_eval = True
    assert not await redis.save_conversation("write-failure", 1, [], "Failure", 60)

    atomic_redis.fail_eval = False
    atomic_redis.fail_set = True
    with pytest.raises(redis.PendingActionPersistenceError):
        await redis.store_pending_action(
            "action-id",
            1,
            "write-failure",
            "create_expense",
            {},
            {},
        )
    assert await atomic_redis.get("chat:pending:action-id") is None


@pytest.mark.asyncio
async def test_pending_action_payload_and_ttl_are_written_atomically(
    atomic_redis: AtomicRedisFake,
) -> None:
    assert await redis.store_pending_action(
        "expiring-action",
        1,
        "conversation",
        "create_expense",
        {},
        {},
        expire_time=5,
    )
    assert await redis.get_pending_action("expiring-action", 1) is not None

    atomic_redis.advance(6)
    assert await redis.get_pending_action("expiring-action", 1) is None


@pytest.mark.parametrize(
    "conversation_id",
    ["", "has spaces", "slash/id", ":colon", "a" * 129, "💸"],
)
def test_malformed_conversation_ids_are_rejected(conversation_id: str) -> None:
    with pytest.raises(ValueError):
        redis.validate_conversation_id(conversation_id)
