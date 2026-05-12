import base64

from openai import AsyncOpenAI

from app.services.ocr.base import BaseOCRService
from app.services.ocr.preprocessor import preprocess_for_ocr
from app.services.llm._http import get_openai_http_client
from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """당신은 신분증 문서 분석 전문가입니다.
제공된 신분증 이미지에서 다음 필드를 정확하게 추출하세요.

[추출 대상]
- 성명(name)
- 주민등록번호(resident_number): 형식 XXXXXX-XXXXXXX
- 주소(address)
- 발급일(issued_date): 형식 YYYY.MM.DD 또는 YYYY년 MM월 DD일

[출력 형식 — JSON만 출력, 다른 텍스트 금지]
{
  "name": "홍길동",
  "resident_number": "900101-1234567",
  "address": "서울특별시 강남구 테헤란로 123",
  "issued_date": "2020.01.15"
}

[규칙]
- 이미지에서 읽을 수 없는 필드는 null로 설정
- 주민등록번호는 정확히 읽은 값만 기록 (추측 금지)
- 주소는 전체 주소를 그대로 기록
- JSON 외 설명 텍스트 절대 금지"""


class IDCardOCRService(BaseOCRService):
    """GPT-4o Vision 기반 신분증 OCR.

    전처리(preprocessor) → base64 인코딩 → GPT-4o vision API → JSON 응답
    GPT-4o 호출 실패 시 원본 이미지로 재시도.
    """

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            http_client=get_openai_http_client(),
        )

    async def extract_text(self, image_bytes: bytes) -> str:
        """신분증 이미지 → JSON 문자열 (name/resident_number/address/issued_date)."""
        try:
            processed = preprocess_for_ocr(image_bytes)
        except Exception as e:
            logger.warning("전처리 실패, 원본 이미지 사용: %s", e)
            processed = image_bytes

        b64 = base64.b64encode(processed).decode("utf-8")

        try:
            result = await self._call_vision(b64, "image/png")
        except Exception as e:
            logger.warning("전처리 이미지 Vision 실패 → 원본으로 재시도: %s", e)
            b64_orig = base64.b64encode(image_bytes).decode("utf-8")
            result = await self._call_vision(b64_orig, "image/jpeg")

        return result

    async def _call_vision(self, b64_image: str, mime: str) -> str:
        response = await self._client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64_image}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": "이 신분증의 정보를 JSON으로 추출해주세요."},
                    ],
                },
            ],
            temperature=0.0,
            max_tokens=300,
        )
        return (response.choices[0].message.content or "").strip()
