from __future__ import annotations

import hmac
import hashlib
import time
import re

from app.config import get_app_secret_key

TOKEN_PREFIX = "LS"
TOKEN_TTL_SECONDS = 600
CARD_NUMBER_PATTERN = re.compile(r"^\d{3}-\d{3}$")


def _signature(payload: str) -> str:
    secret = get_app_secret_key()
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:12]


def make_token(card_number: str, issued_at: int) -> str:
    payload = f"{TOKEN_PREFIX}|{card_number}|{issued_at}"
    return f"{payload}|{_signature(payload)}"


def verify_token(token_str: str) -> str:
    parts = token_str.strip().split("|")
    if len(parts) != 4 or parts[0] != TOKEN_PREFIX:
        raise ValueError("Invalid token format")
    _, card_number, issued_at_raw, signature = parts
    if not CARD_NUMBER_PATTERN.fullmatch(card_number):
        raise ValueError("Invalid card number format")
    if not issued_at_raw.isdigit():
        raise ValueError("Invalid token timestamp")
    issued_at = int(issued_at_raw)
    payload = f"{TOKEN_PREFIX}|{card_number}|{issued_at}"
    expected = _signature(payload)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid token signature")
    now = int(time.time())
    if issued_at > now:
        raise ValueError("Token issued in the future")
    if now - issued_at > TOKEN_TTL_SECONDS:
        raise ValueError("Token expired")
    return card_number
