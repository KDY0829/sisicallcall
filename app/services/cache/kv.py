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


async def get_kv() -> KVStore:
    """Redis가 있으면 Redis를, 없으면 MemoryKV를 반환.

    동시 첫 호출은 Lock으로 직렬화해 이중 초기화를 방지한다.
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
            _kv_singleton = client  # type: ignore[assignment]
        except Exception as e:
            logger.warning("KV: Redis unavailable — in-memory KV (%s)", e)
            _kv_singleton = MemoryKV()

    return _kv_singleton  # type: ignore[return-value]
