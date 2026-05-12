import json
import re
from dataclasses import dataclass


@dataclass
class IDCardFields:
    name: str | None
    resident_number_masked: str | None  # 뒷자리 ******* 마스킹 적용
    address: str | None
    issued_date: str | None
    raw_json: str  # GPT 원본 응답 (감사/디버그용)


_RRN_PATTERN = re.compile(r"(\d{6})-(\d{7})")


def _mask_resident_number(rrn: str | None) -> str | None:
    """주민등록번호 뒷자리 7자리를 *로 마스킹."""
    if not rrn:
        return None
    m = _RRN_PATTERN.search(rrn)
    if m:
        return f"{m.group(1)}-*******"
    # 형식이 맞지 않아도 입력값 그대로 반환 (null 처리 금지)
    return rrn


def parse_id_card_fields(raw_text: str) -> IDCardFields:
    """GPT-4o Vision 응답(JSON 문자열) → IDCardFields.

    JSON 파싱 실패 시 regex로 폴백 파싱.
    """
    raw_text = raw_text.strip()

    # JSON 블록이 ```json ... ``` 로 감싸진 경우 제거
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text).strip()

    data: dict = {}
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        # regex 폴백: "name": "홍길동" 패턴 추출
        for field in ("name", "resident_number", "address", "issued_date"):
            m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', raw_text)
            data[field] = m.group(1) if m else None

    rrn_raw = data.get("resident_number") or data.get("주민등록번호")

    return IDCardFields(
        name=data.get("name") or data.get("성명"),
        resident_number_masked=_mask_resident_number(rrn_raw),
        address=data.get("address") or data.get("주소"),
        issued_date=data.get("issued_date") or data.get("발급일"),
        raw_json=raw_text,
    )


def fields_to_summary(fields: IDCardFields) -> str:
    """IDCardFields → 에이전트가 읽어주기 위한 요약 문자열."""
    parts = []
    if fields.name:
        parts.append(f"성함 {fields.name}")
    if fields.resident_number_masked:
        parts.append(f"주민번호 앞자리 {fields.resident_number_masked.split('-')[0]}")
    if fields.address:
        parts.append(f"주소 {fields.address}")
    if fields.issued_date:
        parts.append(f"발급일 {fields.issued_date}")
    return ", ".join(parts) if parts else "신분증 정보를 읽을 수 없었어요."
