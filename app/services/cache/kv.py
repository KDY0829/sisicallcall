import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class KVStore:
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, *, ex: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...

    async def hset(self, key: str, mapping: Mapping[str, str]) -> None: ...
    async def hgetall(self, key: str) -> dict[str, str]: ...
    async def hincrby(self, key: str, field: str, amount: int) -> int: ...
    async def expire(self, key: str, ttl_seconds: int) -> None: ...


@dataclass
class _Entry:
    value: Any
    expires_at: float | None


class MemoryKV(KVStore):
    """Redis 대체용 in-memory KV.

    - 프로세스 재시작 시 데이터는 사라짐 (개발/로컬 실행용).
    - TTL 은 best-effort: 접근 시점에만 만료 정리.
    """

    def __init__(self) -> None:
        self._data: dict[str, _Entry] = {}

    def _now(self) -> float:
        return time.time()

    def _is_expired(self, e: _Entry) -> bool:
        return e.expires_at is not None and e.expires_at <= self._now()

    def _get_entry(self, key: str) -> _Entry | None:
        e = self._data.get(key)
        if not e:
            return None
        if self._is_expired(e):
            self._data.pop(key, None)
            return None
        return e

    async def get(self, key: str) -> str | None:
        e = self._get_entry(key)
        if e is None:
            return None
        if isinstance(e.value, str):
            return e.value
        return json.dumps(e.value, ensure_ascii=False)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        expires_at = self._now() + ex if ex else None
        self._data[key] = _Entry(value=value, expires_at=expires_at)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def hset(self, key: str, mapping: Mapping[str, str]) -> None:
        e = self._get_entry(key)
        if e is None:
            obj: dict[str, str] = {}
            self._data[key] = _Entry(value=obj, expires_at=None)
            e = self._data[key]
        if not isinstance(e.value, dict):
            e.value = {}
        e.value.update({str(k): str(v) for k, v in mapping.items()})

    async def hgetall(self, key: str) -> dict[str, str]:
        e = self._get_entry(key)
        if e is None or not isinstance(e.value, dict):
            return {}
        return {str(k): str(v) for k, v in e.value.items()}

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        await self.hset(key, mapping={})
        e = self._get_entry(key)
        assert e is not None and isinstance(e.value, dict)
        cur = int(e.value.get(field, "0") or "0")
        nxt = cur + int(amount)
        e.value[field] = str(nxt)
        return nxt

    async def expire(self, key: str, ttl_seconds: int) -> None:
        e = self._get_entry(key)
        if e is None:
            return
        e.expires_at = self._now() + ttl_seconds


_kv_singleton: KVStore | None = None
_kv_init_lock: asyncio.Lock | None = None


def _get_init_lock() -> asyncio.Lock:
    global _kv_init_lock
    if _kv_init_lock is None:
        _kv_init_lock = asyncio.Lock()
    return _kv_init_lock


def _redis_is_configured() -> bool:
    url = (settings.redis_url or "").strip()
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"redis", "rediss"}


def _is_redis_conn_error(e: Exception) -> bool:
    try:
        import redis.exceptions
        return isinstance(e, (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError))
    except ImportError:
        return False


class _RedisWithFallback(KVStore):
    """Redis 연결이 끊기면 MemoryKV로 자동 전환하는 래퍼.

    ping은 성공했으나 실제 operation에서 연결이 끊길 때를 처리.
    전환 후 _kv_singleton도 MemoryKV로 교체해 새 인스턴스도 fallback을 받는다.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._mem = MemoryKV()
        self._down = False

    def _fail(self, e: Exception) -> MemoryKV:
        global _kv_singleton
        if not self._down:
            logger.warning("KV: Redis 연결 끊김 → in-memory fallback (%s)", e)
            self._down = True
            _kv_singleton = self._mem
        return self._mem

    async def get(self, key: str) -> str | None:
        if self._down:
            return await self._mem.get(key)
        try:
            return await self._client.get(key)
        except Exception as e:
            if _is_redis_conn_error(e):
                return await self._fail(e).get(key)
            raise

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        if self._down:
            return await self._mem.set(key, value, ex=ex)
        try:
            await self._client.set(key, value, ex=ex)
        except Exception as e:
            if _is_redis_conn_error(e):
                return await self._fail(e).set(key, value, ex=ex)
            raise

    async def delete(self, key: str) -> None:
        if self._down:
            return await self._mem.delete(key)
        try:
            await self._client.delete(key)
        except Exception as e:
            if _is_redis_conn_error(e):
                return await self._fail(e).delete(key)
            raise

    async def hset(self, key: str, mapping: Mapping[str, str]) -> None:
        if self._down:
            return await self._mem.hset(key, mapping)
        try:
            await self._client.hset(key, mapping=mapping)
        except Exception as e:
            if _is_redis_conn_error(e):
                return await self._fail(e).hset(key, mapping)
            raise

    async def hgetall(self, key: str) -> dict[str, str]:
        if self._down:
            return await self._mem.hgetall(key)
        try:
            return await self._client.hgetall(key)
        except Exception as e:
            if _is_redis_conn_error(e):
                return await self._fail(e).hgetall(key)
            raise

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        if self._down:
            return await self._mem.hincrby(key, field, amount)
        try:
            return await self._client.hincrby(key, field, amount)
        except Exception as e:
            if _is_redis_conn_error(e):
                return await self._fail(e).hincrby(key, field, amount)
            raise

    async def expire(self, key: str, ttl_seconds: int) -> None:
        if self._down:
            return await self._mem.expire(key, ttl_seconds)
        try:
            await self._client.expire(key, ttl_seconds)
        except Exception as e:
            if _is_redis_conn_error(e):
                return await self._fail(e).expire(key, ttl_seconds)
            raise


async def get_kv() -> KVStore:
    """Redis가 있으면 Redis를, 없으면 MemoryKV를 반환.

    동시 첫 호출은 Lock으로 직렬화해 이중 초기화를 방지한다.
    Redis 연결이 runtime에 끊겨도 _RedisWithFallback이 자동으로 MemoryKV로 전환한다.
    """
    global _kv_singleton
    if _kv_singleton is not None:
        return _kv_singleton

    async with _get_init_lock():
        if _kv_singleton is not None:  # 락 획득 후 재확인
            return _kv_singleton

        if not _redis_is_configured():
            logger.warning("redis_url not set — in-memory KV (non-persistent)")
            _kv_singleton = MemoryKV()
            return _kv_singleton

        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await asyncio.wait_for(client.ping(), timeout=3.0)
            logger.info("KV: using Redis (%s)", settings.redis_url)
            _kv_singleton = _RedisWithFallback(client)
        except Exception as e:
            logger.warning("KV: Redis unavailable — in-memory KV (%s)", e)
            _kv_singleton = MemoryKV()

    return _kv_singleton  # type: ignore[return-value]
