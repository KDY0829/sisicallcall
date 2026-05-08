from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from app.services.ocr.session import OCRSessionService
from app.services.ocr.tesseract import TesseractOCRService
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

_session_svc = OCRSessionService()
_ocr_svc = TesseractOCRService()

_OCR_PAGE_HTML = (
    Path(__file__).parent.parent.parent / "static" / "ocr_upload.html"
).read_text(encoding="utf-8")


async def _run_extraction(ocr_id: str, image_bytes: bytes) -> None:
    """백그라운드에서 OCR 실행 후 세션 상태 갱신."""
    try:
        extracted_text = await _ocr_svc.extract_text(image_bytes)
        await _session_svc.set_extracted(ocr_id, extracted_text)
    except Exception as exc:
        logger.exception("ocr extract 실패 ocr_id=%s: %s", ocr_id, exc)
        await _session_svc.set_failed(ocr_id, reason=str(exc))


@router.post("/{ocr_id}/extract", status_code=202)
async def extract_text(ocr_id: str, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """폰에서 업로드한 문서 사진 → OCR 텍스트 추출 (백그라운드 처리, 202 Accepted)."""
    session = await _session_svc.get_session(ocr_id)
    if not session:
        raise HTTPException(status_code=404, detail="OCR 세션이 없거나 만료됨")
    if session.get("status") not in ("pending", "extracting"):
        raise HTTPException(status_code=409, detail=f"잘못된 상태: {session.get('status')}")

    await _session_svc.set_extracting(ocr_id)
    image_bytes = await file.read()
    background_tasks.add_task(_run_extraction, ocr_id, image_bytes)

    return {"ocr_id": ocr_id, "status": "extracting"}


@router.get("/{ocr_id}/status")
async def get_ocr_status(ocr_id: str):
    """OCR 세션 상태 폴링."""
    session = await _session_svc.get_session(ocr_id)
    if not session:
        raise HTTPException(status_code=404, detail="OCR 세션이 없거나 만료됨")
    return {
        "ocr_id": ocr_id,
        "status": session.get("status", "unknown"),
        "doc_type": session.get("doc_type", ""),
        "char_count": len(session.get("extracted_text", "")),
    }


@router.get("/{ocr_id}", response_class=HTMLResponse)
async def ocr_page(ocr_id: str) -> HTMLResponse:
    """SMS 링크에서 진입하는 문서 사진 업로드 페이지."""
    return HTMLResponse(content=_OCR_PAGE_HTML)
