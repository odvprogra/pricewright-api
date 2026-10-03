"""Refresh tokens: how a signed-in session continues without the password (ADR-0007).

A family starts at login. Every refresh uses up the presented token and issues its successor
(rotation). Presenting a used token again means it leaked, so the whole family is revoked
(RFC 9700 §4.14.2). A session ends after 14 days unused or 30 days in total, whichever comes first.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewright.domain.digests import digest

IDLE_LIFETIME = timedelta(days=14)
ABSOLUTE_LIFETIME = timedelta(days=30)
# An identifiable prefix lets secret scanners recognize leaked tokens (like GitHub's ``ghr_``).
REFRESH_PREFIX = "pwr_"


def new_refresh_token() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(32)


@dataclass(slots=True)
class RefreshToken:
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    family_id: uuid.UUID
    token_digest: str
    expires_at: datetime
    family_expires_at: datetime
    used_at: datetime | None = None
    revoked_at: datetime | None = None

    @classmethod
    def start_family(
        cls, *, tenant_id: uuid.UUID, user_id: uuid.UUID, token: str, now: datetime
    ) -> RefreshToken:
        family_expires_at = now + ABSOLUTE_LIFETIME
        return cls(
            id=uuid.uuid7(),
            tenant_id=tenant_id,
            user_id=user_id,
            family_id=uuid.uuid7(),
            token_digest=digest(token),
            expires_at=min(now + IDLE_LIFETIME, family_expires_at),
            family_expires_at=family_expires_at,
        )

    def successor(self, *, token: str, now: datetime) -> RefreshToken:
        """The token that replaces this one; it never outlives the family."""
        return RefreshToken(
            id=uuid.uuid7(),
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            family_id=self.family_id,
            token_digest=digest(token),
            expires_at=min(now + IDLE_LIFETIME, self.family_expires_at),
            family_expires_at=self.family_expires_at,
        )

    @property
    def was_used(self) -> bool:
        return self.used_at is not None

    def is_usable(self, now: datetime) -> bool:
        return self.used_at is None and self.revoked_at is None and now < self.expires_at
