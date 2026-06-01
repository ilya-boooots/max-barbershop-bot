from __future__ import annotations

from typing import Final
import re

import numpy as np

try:
    import cv2
except Exception:
    cv2 = None

CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{3}\b")


def decode_qr_code_from_bytes(image_bytes: bytes) -> str | None:
    if cv2 is None:
        return None
    if not image_bytes:
        return None
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        return None
    detector = cv2.QRCodeDetector()
    payload, _, _ = detector.detectAndDecode(image)
    if not payload:
        return None
    payload = payload.strip()
    if CODE_PATTERN.fullmatch(payload):
        return payload
    match = CODE_PATTERN.search(payload)
    if match:
        return match.group(0)
    return None
