from __future__ import annotations

from io import BytesIO

import qrcode


def generate_qr_png_bytes(data: str) -> bytes:
    image = qrcode.make(data)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
