"""credential_for(user, audience)로 자격 증명을 꺼내는 유일한 지점."""

from govops.broker.credentials import (
    Audience,
    CredentialError,
    CredentialSourceError,
    UnknownAudienceError,
    UnknownUserError,
    credential_for,
    known_users,
)

__all__ = [
    "Audience",
    "CredentialError",
    "CredentialSourceError",
    "UnknownAudienceError",
    "UnknownUserError",
    "credential_for",
    "known_users",
]
