import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_OCR_SESSION_TTL = 600  # 10분


def _key(ocr_id: str) -> str:
    return f"ocr:session:{ocr_id}"


class OCRSessionService:
    """OCR 세션 Redis 저장소.

    status flow: pending → extracting → extracted | failed
    추출 결과(extracted_text)는 extracted 시 저장.
    """

    def __init__(self) -> None:
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def create_session(
        self,
        *,
        tenant_id: str,
        customer_phone: str,
        call_id: str,
        doc_type: str = "general",
    ) -> str:
        """OCR 세션 생성 후 ocr_id 반환.

        doc_type: "general" | "prescription" | "id_card" | "receipt" | "contract"
        """
        ocr_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._redis.hset(_key(ocr_id), mapping={
            "ocr_id": ocr_id,
            "tenant_id": tenant_id,
            "customer_phone": customer_phone,
            "call_id": call_id,
            "doc_type": doc_type,
            "status": "pending",
            "extracted_text": "",
            "created_at": now,
        })
        await self._redis.expire(_key(ocr_id), _OCR_SESSION_TTL)
        logger.info("ocr session 생성 ocr_id=%s tenant=%s doc_type=%s", ocr_id, tenant_id, doc_type)
        return ocr_id

    async def get_session(self, ocr_id: str) -> dict | None:
        data = await self._redis.hgetall(_key(ocr_id))
        return data if data else None

    async def set_extracting(self, ocr_id: str) -> None:
        await self._redis.hset(_key(ocr_id), "status", "extracting")

    async def set_extracted(self, ocr_id: str, extracted_text: str) -> None:
        await self._redis.hset(_key(ocr_id), mapping={
            "status": "extracted",
            "extracted_text": extracted_text,
        })
        logger.info("ocr extracted ocr_id=%s chars=%d", ocr_id, len(extracted_text))

    async def set_failed(self, ocr_id: str, reason: str = "") -> None:
        mapping: dict = {"status": "failed"}
        if reason:
            mapping["fail_reason"] = reason
        await self._redis.hset(_key(ocr_id), mapping=mapping)
        logger.warning("ocr failed ocr_id=%s reason=%s", ocr_id, reason)
