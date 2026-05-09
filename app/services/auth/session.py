import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.services.auth.events import publish_auth_event
from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_AUTH_SESSION_TTL = 600  # 10분


def _key(auth_id: str) -> str:
    return f"auth:session:{auth_id}"


class AuthSessionService:
    def __init__(self) -> None:
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def create_session(
        self,
        *,
        tenant_id: str,
        customer_ref: str,
        customer_phone: str,
        call_id: str,
    ) -> str:
        auth_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._redis.hset(_key(auth_id), mapping={
            "auth_id": auth_id,
            "tenant_id": tenant_id,
            "customer_ref": customer_ref,
            "customer_phone": customer_phone,
            "call_id": call_id,
            "status": "pending",
            "liveness_passed": "true",  # liveness 단계 미구현 — 항상 통과
            "ocr_passed": "false",
            "face_verified": "false",
            "face_attempts": "0",
            "created_at": now,
        })
        await self._redis.expire(_key(auth_id), _AUTH_SESSION_TTL)
        logger.info("auth session 생성 auth_id=%s tenant=%s", auth_id, tenant_id)
        return auth_id

    async def get_session(self, auth_id: str) -> dict | None:
        data = await self._redis.hgetall(_key(auth_id))
        return data if data else None

    async def update_status(self, auth_id: str, status: str) -> None:
        await self._redis.hset(_key(auth_id), "status", status)

    async def set_liveness_passed(self, auth_id: str) -> None:
        await self._redis.hset(_key(auth_id), mapping={
            "liveness_passed": "true",
            "status": "liveness_passed",
        })

    async def increment_face_attempts(self, auth_id: str) -> int:
        return await self._redis.hincrby(_key(auth_id), "face_attempts", 1)

    async def set_face_verified(self, auth_id: str) -> None:
        # OCR 도 통과해야 verified — 한쪽만 통과면 face_verified 로 stuck.
        session = await self.get_session(auth_id)
        if not session:
            return
        new_status = "verified" if session.get("ocr_passed") == "true" else "face_verified"
        await self._redis.hset(_key(auth_id), mapping={
            "face_verified": "true",
            "status": new_status,
        })
        # Phase 1 pub/sub — listener 가 자율 발화 트리거
        if new_status == "verified":
            await publish_auth_event(auth_id, "verified")
        else:
            await publish_auth_event(auth_id, "face_verified_partial")

    async def set_ocr_passed(self, auth_id: str) -> None:
        # face 도 통과해야 verified — 한쪽만 통과면 ocr_passed 로 stuck.
        session = await self.get_session(auth_id)
        if not session:
            return
        new_status = "verified" if session.get("face_verified") == "true" else "ocr_passed"
        await self._redis.hset(_key(auth_id), mapping={
            "ocr_passed": "true",
            "status": new_status,
        })
        if new_status == "verified":
            await publish_auth_event(auth_id, "verified")
        else:
            await publish_auth_event(auth_id, "ocr_passed_partial")

    async def update_customer_ref(self, auth_id: str, customer_ref: str) -> None:
        # OCR 매칭으로 확인된 phone 을 customer_ref 로 갱신 — face 매칭 시 사용.
        await self._redis.hset(_key(auth_id), "customer_ref", customer_ref)

    async def set_blocked(self, auth_id: str) -> None:
        await self._redis.hset(_key(auth_id), "status", "blocked")
        await publish_auth_event(auth_id, "blocked")
