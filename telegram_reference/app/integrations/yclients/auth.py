from __future__ import annotations

from .dto import YClientsCredentials


def build_auth_headers(credentials: YClientsCredentials) -> dict[str, str]:
    """Build YClients auth headers.

    According to project source-of-truth PDF, auth format is:
    Authorization: Bearer <partner_token>, User <user_token>
    """

    authorization = f"Bearer {credentials.partner_token}"
    if credentials.user_token:
        authorization = f"{authorization}, User {credentials.user_token}"

    return {
        "Authorization": authorization,
        "Accept": "application/vnd.yclients.v2+json",
        "Content-Type": "application/json",
    }
