from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.v1.admin_auth import get_current_admin_user
from app.repositories.rag_document_repo import (
    get_rag_document_for_tenant,
    list_rag_documents_for_tenant,
    soft_delete_rag_document_for_tenant,
)

router = APIRouter()


def _request_id() -> str:
    return f"req-{uuid.uuid4().hex[:8]}"


def _current_admin_tenant_id(current_admin: dict[str, Any]) -> str:
    user = current_admin.get("user") or {}
    tenant_id = str(user.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin tenant",
        )
    return tenant_id


def _validate_path_tenant_id(path_tenant_id: str, current_admin: dict[str, Any]) -> str:
    jwt_tenant_id = _current_admin_tenant_id(current_admin)
    if path_tenant_id.strip().lower() != jwt_tenant_id.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant 정보가 일치하지 않습니다.",
        )
    return jwt_tenant_id


async def _process_pdf_document(
    *,
    tenant_id: str,
    pdf_path: str,
    file_name: str,
) -> str:
    from app.services.chunking.pdf_processor import PDFProcessor
    from app.services.embedding import get_embedder
    from app.services.rag.chroma import ChromaRAGService

    processor = PDFProcessor(
        embedder=get_embedder(),
        rag=ChromaRAGService(),
    )
    return await processor.process(
        pdf_path=pdf_path,
        tenant_id=tenant_id,
        file_name=file_name,
        industry="general",
    )


async def _delete_document_vectors(document_id: str, tenant_id: str) -> None:
    from app.services.rag.chroma import ChromaRAGService

    await ChromaRAGService().delete_by_document(document_id, tenant_id)


@router.get("/{tenant_id}/documents")
async def list_documents(
    tenant_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    jwt_tenant_id = _validate_path_tenant_id(tenant_id, current_admin)
    result = await list_rag_documents_for_tenant(
        tenant_id=jwt_tenant_id,
        status=status_filter,
        offset=offset,
        limit=limit,
    )
    return {
        "data": result,
        "request_id": _request_id(),
    }


@router.get("/{tenant_id}/documents/{document_id}")
async def get_document(
    tenant_id: str,
    document_id: str,
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    jwt_tenant_id = _validate_path_tenant_id(tenant_id, current_admin)
    record = await get_rag_document_for_tenant(document_id, jwt_tenant_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document not found: {document_id!r}",
        )
    return {
        "data": record,
        "request_id": _request_id(),
    }


@router.post("/{tenant_id}/documents")
async def upload_document(
    tenant_id: str,
    file: UploadFile = File(...),
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    jwt_tenant_id = _validate_path_tenant_id(tenant_id, current_admin)
    file_name = Path(file.filename or "").name
    if not file_name.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported.",
        )

    tmp_path = ""
    try:
        suffix = Path(file_name).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            tmp.write(await file.read())

        document_id = await _process_pdf_document(
            tenant_id=jwt_tenant_id,
            pdf_path=tmp_path,
            file_name=file_name,
        )
        record = await get_rag_document_for_tenant(document_id, jwt_tenant_id)
        return {
            "data": {
                "document_id": document_id,
                "status": (record or {}).get("status") or "ready",
            },
            "request_id": _request_id(),
        }
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


@router.delete("/{tenant_id}/documents/{document_id}")
async def delete_document(
    tenant_id: str,
    document_id: str,
    current_admin: dict[str, Any] = Depends(get_current_admin_user),
):
    jwt_tenant_id = _validate_path_tenant_id(tenant_id, current_admin)
    record = await get_rag_document_for_tenant(document_id, jwt_tenant_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document not found: {document_id!r}",
        )

    try:
        await _delete_document_vectors(document_id, jwt_tenant_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"document vector delete failed: {exc}",
        ) from exc

    deleted = await soft_delete_rag_document_for_tenant(document_id, jwt_tenant_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document not found: {document_id!r}",
        )

    return {
        "data": {
            "document_id": document_id,
            "deleted": True,
        },
        "request_id": _request_id(),
    }


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: str):
    raise NotImplementedError


@router.post("/")
async def create_tenant():
    raise NotImplementedError
