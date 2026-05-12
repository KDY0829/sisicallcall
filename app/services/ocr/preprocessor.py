import io

import cv2
import numpy as np
from PIL import Image


def preprocess_for_ocr(image_bytes: bytes) -> bytes:
    """신분증/문서 이미지 OCR 품질 향상을 위한 전처리.

    처리 순서:
      1. 그레이스케일 변환  — 컬러 노이즈 제거
      2. 업스케일           — 저해상도 이미지 보완 (300 DPI 목표)
      3. CLAHE 대비 강화    — 조명 불균일 보정
      4. 가우시안 블러      — 미세 노이즈 제거
      5. Otsu 이진화        — 글자/배경 명확히 분리
      6. 모폴로지 클로징    — 글자 획 끊김 복원

    Returns:
        전처리된 이미지의 PNG bytes
    """
    # PIL → numpy (BGR)
    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    # 1. 그레이스케일
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 2. 업스케일 (짧은 변이 1200px 미만이면 2배 확대)
    h, w = gray.shape
    if min(h, w) < 1200:
        gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

    # 3. CLAHE — 지역 대비 강화 (조명 그림자 보정)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # 4. 가우시안 블러 (미세 노이즈 제거)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    # 5. Otsu 이진화 (자동 임계값 결정)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 6. 모폴로지 클로징 — 획 끊김 복원 (글자 인식률 향상)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    processed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # numpy → PNG bytes
    _, buf = cv2.imencode(".png", processed)
    return buf.tobytes()
