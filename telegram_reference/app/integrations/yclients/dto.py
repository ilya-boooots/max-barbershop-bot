from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class YClientsCredentials:
    partner_token: str
    user_token: str | None
    company_id: str


@dataclass(frozen=True)
class YClientsCredentialsDiagnostics:
    partner_token_masked: str
    user_token_masked: str
    company_id_masked: str
    source: str


@dataclass(frozen=True)
class YClientsHealthCheckResult:
    ok: bool
    status_code: int | None
    short_message: str
