from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _database_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _is_uuid(value: str | None) -> bool:
    try:
        UUID(str(value))
        return True
    except Exception:
        return False


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _row_to_document(row: Any) -> dict:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "file_name": row["file_name"],
        "file_type": row["file_type"],
        "chunk_count": row["chunk_count"],
        "status": row["status"],
        "chroma_collection": row["chroma_collection"],
        "uploaded_at": _iso(row["uploaded_at"]),
        "indexed_at": _iso(row["indexed_at"]),
    }


async def list_rag_documents_for_tenant(
    tenant_id: str,
    *,
    status: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    if not _is_uuid(tenant_id):
        return {"items": [], "total": 0, "offset": max(0, offset), "limit": max(1, min(limit, 100))}

    normalized_offset = max(0, int(offset))
    normalized_limit = max(1, min(int(limit), 100))
    normalized_status = status.strip() if status else None

    where = ["tenant_id = $1::uuid", "deleted_at IS NULL"]
    params: list[Any] = [tenant_id]
    if normalized_status:
        params.append(normalized_status)
        where.append(f"status = ${len(params)}")

    where_sql = " AND ".join(where)
    count_sql = f"""
        SELECT COUNT(*)::int AS total
        FROM rag_documents
        WHERE {where_sql}
    """

    list_params = list(params)
    list_params.append(normalized_offset)
    offset_pos = len(list_params)
    list_params.append(normalized_limit)
    limit_pos = len(list_params)
    list_sql = f"""
        SELECT
            id,
            tenant_id,
            file_name,
            file_type,
            chunk_count,
            status,
            chroma_collection,
            uploaded_at,
            indexed_at
        FROM rag_documents
        WHERE {where_sql}
        ORDER BY uploaded_at DESC
        OFFSET ${offset_pos}
        LIMIT ${limit_pos}
    """

    conn = None
    try:
        conn = await asyncpg.connect(_database_url())
        total_row = await conn.fetchrow(count_sql, *params)
        rows = await conn.fetch(list_sql, *list_params)
        return {
            "items": [_row_to_document(row) for row in rows],
            "total": int((total_row or {})["total"] or 0) if total_row else 0,
            "offset": normalized_offset,
            "limit": normalized_limit,
        }
    except Exception as exc:
        logger.warning("rag document list failed tenant_id=%s err=%s", tenant_id, exc)
        return {"items": [], "total": 0, "offset": normalized_offset, "limit": normalized_limit}
    finally:
        if conn is not None:
            await conn.close()


async def get_rag_document_for_tenant(
    document_id: str,
    tenant_id: str,
) -> dict | None:
    if not _is_uuid(document_id) or not _is_uuid(tenant_id):
        return None

    conn = None
    try:
        conn = await asyncpg.connect(_database_url())
        row = await conn.fetchrow(
            """
            SELECT
                id,
                tenant_id,
                file_name,
                file_type,
                chunk_count,
                status,
                chroma_collection,
                uploaded_at,
                indexed_at
            FROM rag_documents
            WHERE id = $1::uuid
              AND tenant_id = $2::uuid
              AND deleted_at IS NULL
            LIMIT 1
            """,
            document_id,
            tenant_id,
        )
        return _row_to_document(row) if row else None
    except Exception as exc:
        logger.warning(
            "rag document lookup failed document_id=%s tenant_id=%s err=%s",
            document_id,
            tenant_id,
            exc,
        )
        return None
    finally:
        if conn is not None:
            await conn.close()


async def soft_delete_rag_document_for_tenant(
    document_id: str,
    tenant_id: str,
) -> bool:
    if not _is_uuid(document_id) or not _is_uuid(tenant_id):
        return False

    conn = None
    try:
        conn = await asyncpg.connect(_database_url())
        result = await conn.execute(
            """
            UPDATE rag_documents
            SET deleted_at = now()
            WHERE id = $1::uuid
              AND tenant_id = $2::uuid
              AND deleted_at IS NULL
            """,
            document_id,
            tenant_id,
        )
        return result.upper().startswith("UPDATE 1")
    except Exception as exc:
        logger.warning(
            "rag document soft delete failed document_id=%s tenant_id=%s err=%s",
            document_id,
            tenant_id,
            exc,
        )
        return False
    finally:
        if conn is not None:
            await conn.close()
