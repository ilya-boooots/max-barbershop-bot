"""Authorization helpers for YClients API requests."""

from __future__ import annotations

from .dto import YClientsCredentials
from .exceptions import YClientsConfigError

YCLIENTS_ACCEPT_HEADER = "application/vnd.yclients.v2+json"


def build_authorization_header(partner_token: str, user_token: str | None = None) -> str:
    """Build the YClients Authorization header value.

    The local YClients REST API PDF describes the v2
    format as ``Bearer <partner_token>, User <user_token>`` when a user token is
    available, and ``Bearer <partner_token>`` for partner-token-only requests.
    """

    partner_token = partner_token.strip()
    user_token = user_token.strip() if user_token else None
    if not partner_token:
        raise YClientsConfigError("YCLIENTS_PARTNER_TOKEN is required for YClients API requests")

    authorization = f"Bearer {partner_token}"
    if user_token:
        authorization = f"{authorization}, User {user_token}"
    return authorization


def build_auth_headers(credentials: YClientsCredentials) -> dict[str, str]:
    """Build safe, standard YClients request headers from credentials."""

    return {
        "Authorization": build_authorization_header(
            credentials.partner_token,
            credentials.user_token,
        ),
        "Accept": YCLIENTS_ACCEPT_HEADER,
        "Content-Type": "application/json",
    }
