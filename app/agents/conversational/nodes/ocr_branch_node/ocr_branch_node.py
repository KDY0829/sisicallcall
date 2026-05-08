import os

from app.agents.conversational.prompts.fallback_phrases import get_inquiry_phrase
from app.agents.conversational.state import CallState
from app.services.llm.gpt4o_mini import GPT4OMiniService
from app.services.ocr.session import OCRSessionService
from app.services.session.redis_session import RedisSessionService
from app.services.sms import get_sms_service
from app.utils.config import settings

_llm = GPT4OMiniService()
_ocr_session_svc = OCRSessionService()
_call_session_svc = RedisSessionService()
_sms_svc = get_sms_service()

_POLITE_SMS_FAILED = "문서 업로드 링크 발송에 문제가 생겼어요. 잠시 후 다시 시도해주세요."
_POLITE_SMS_SENT = (
    "문서 내용을 확인할 수 있도록 휴대폰으로 업로드 링크를 보내드렸어요. "
    "사진 업로드가 끝나면 저에게 말씀해주세요. 바로 확인해드릴게요."
)
_POLITE_NOT_RECEIVED = "아직 문서가 도착하지 않았어요. 업로드 후 다시 알려주세요."
_POLITE_EXTRACTING = "문서를 읽고 있어요. 잠시만 기다려주세요."
_POLITE_FAILED = "죄송해요, 문서 인식에 문제가 생겼어요. 상담원으로 연결해드릴게요."

_OCR_HUMANIZE_PROMPT = """당신은 매장 전화 상담 AI 입니다.
사용자가 보낸 문서에서 추출된 텍스트가 주어집니다.
이를 바탕으로 친절한 음성 안내로 핵심 내용을 두세 문장으로 요약해 전달하세요.

[지침]
- 문서의 핵심 정보만 요약. 불필요한 반복 금지.
- 추출 텍스트에 없는 정보는 절대 추측하지 마세요.
- "OCR", "추출", "텍스트", "문서 인식" 같은 기술 표현 금지. 매장 직원처럼 답하세요.
- 출력은 응답 텍스트만. 따옴표/머릿말 금지."""


def _polite_no_phone(industry: str) -> str:
    return f"문서 안내를 위한 정보가 부족해요. {get_inquiry_phrase(industry)}."


async def _humanize_text(extracted_text: str) -> str:
    try:
        text = await _llm.generate(
            system_prompt=_OCR_HUMANIZE_PROMPT,
            user_message=f"[추출된 문서 내용]\n{extracted_text}",
            temperature=0.2,
            max_tokens=300,
        )
        return text.strip().strip('"').strip("'") or extracted_text[:200]
    except Exception as exc:
        print(f"[ocr_branch] LLM humanize 실패 → 원문 앞부분 반환: {exc}")
        return extracted_text[:200]


async def _create_new_ocr(call_id: str, tenant_id: str, customer_phone: str) -> dict:
    ocr_id = await _ocr_session_svc.create_session(
        tenant_id=tenant_id,
        customer_phone=customer_phone,
        call_id=call_id,
    )
    print(f"[ocr_branch] 세션 생성 ocr_id={ocr_id}")

    upload_url = f"{settings.auth_web_base_url}/ocr/{ocr_id}"
    sms_body = f"[시시콜콜] 문서 업로드 링크입니다.\n{upload_url}"
    sent = await _sms_svc.send_sms(to=customer_phone, body=sms_body)
    print(f"[ocr_branch] SMS 발송 sent={sent} to={customer_phone}")

    if not sent:
        return {"response_text": _POLITE_SMS_FAILED}

    await _call_session_svc.set_ocr_id(call_id, ocr_id)
    print(f"[ocr_branch] 통화 세션에 ocr_id 저장")
    return {"response_text": _POLITE_SMS_SENT}


async def ocr_branch_node(state: CallState) -> dict:
    tenant_id = state["tenant_id"]
    tenant_industry = state.get("tenant_industry", "")
    call_id = state["call_id"]

    customer_phone = os.getenv("SMS_TEST_RECIPIENT", "")
    if not customer_phone:
        print("[ocr_branch] SMS_TEST_RECIPIENT 미설정 → polite_no_phone")
        return {"response_text": _polite_no_phone(tenant_industry)}

    existing_ocr_id = await _call_session_svc.get_ocr_id(call_id)
    if existing_ocr_id:
        session = await _ocr_session_svc.get_session(existing_ocr_id)
        if session is None:
            print(f"[ocr_branch] 기존 ocr_id={existing_ocr_id} TTL 만료 → 재발급")
            return await _create_new_ocr(call_id, tenant_id, customer_phone)

        status = session.get("status", "")
        print(f"[ocr_branch] 기존 ocr_id={existing_ocr_id} status={status}")

        if status == "extracted":
            extracted_text = session.get("extracted_text", "")
            await _call_session_svc.clear_ocr_id(call_id)
            if not extracted_text.strip():
                return {"response_text": "문서에서 텍스트를 찾을 수 없었어요. 다시 촬영해서 보내주시겠어요?"}
            text = await _humanize_text(extracted_text)
            return {"response_text": text}

        if status == "failed":
            await _call_session_svc.clear_ocr_id(call_id)
            return {"response_text": _POLITE_FAILED}

        if status == "extracting":
            return {"response_text": _POLITE_EXTRACTING}

        # pending — 아직 업로드 안 됨
        return {"response_text": _POLITE_NOT_RECEIVED}

    return await _create_new_ocr(call_id, tenant_id, customer_phone)
