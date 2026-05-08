import asyncio

import asyncpg

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 3
_POOL_COMMAND_TIMEOUT = 5.0

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                dsn=settings.database_url,
                min_size=_POOL_MIN_SIZE,
                max_size=_POOL_MAX_SIZE,
                command_timeout=_POOL_COMMAND_TIMEOUT,
            )
    return _pool


async def insert_ocr_audit_log(
    *,
    ocr_id: str,
    call_id: str,
    tenant_id: str,
    doc_type: str,
    status: str,
    char_count: int = 0,
    fail_reason: str | None = None,
) -> None:
    """OCR 추출 결과를 audit log 테이블에 best-effort INSERT."""
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ocr_audit_logs
                    (ocr_id, call_id, tenant_id, doc_type, status, char_count, fail_reason)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                ocr_id,
                call_id,
                tenant_id,
                doc_type,
                status,
                char_count,
                fail_reason,
            )
        logger.info(
            "ocr_audit_log inserted ocr_id=%s status=%s chars=%d",
            ocr_id, status, char_count,
        )
    except Exception as e:
        logger.warning("ocr_audit_log insert failed ocr_id=%s err=%s", ocr_id, e)
