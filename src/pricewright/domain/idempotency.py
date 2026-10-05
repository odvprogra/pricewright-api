"""Idempotency keys: a retried creation returns what the first request created (ADR-0022).

A client may send an ``Idempotency-Key`` with a request that creates something. The first request
with that key runs, and its unit of work remembers the key, a fingerprint of the request and what
it created, for 24 hours. A retry of the same request gets that resource back instead of creating
another; the same key with a different request is refused. A key belongs to the caller who sent
it, so nobody replays someone else's request.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditResourceType
from pricewright.domain.errors import ConflictError, RuleViolationError

MAX_KEY_LENGTH = 255  # Stripe's limit
KEY_LIFETIME = timedelta(hours=24)  # Stripe keeps keys for at least 24 hours
# Visible ASCII without the double quote and the backslash, the two characters a Structured
# Field string escapes (RFC 9651 §3.3.3), so a key travels the same quoted or bare.
_KEY = re.compile(rf"[!#-\[\]-~]{{1,{MAX_KEY_LENGTH}}}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


class InvalidIdempotencyKeyError(RuleViolationError):
    code = "invalid_idempotency_key"


class IdempotencyKeyInUseError(ConflictError):
    """Another request with the key is still running: retry once it has finished."""

    code = "idempotency_key_in_use"


class IdempotencyKeyReusedError(RuleViolationError):
    """The key already went with a different request: a client bug, never a retry."""

    code = "idempotency_key_reused"


@dataclass(frozen=True, slots=True)
class IdempotentRequest:
    """A request sent with an ``Idempotency-Key``: the key and a fingerprint of the request."""

    key: str
    fingerprint: str
    """A SHA-256 in lower-case hex of what makes the request itself: the API hashes the method,
    the path and the body."""

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(self.key):
            raise InvalidIdempotencyKeyError(
                f"an Idempotency-Key has 1 to {MAX_KEY_LENGTH} visible ASCII characters, "
                "without double quotes or backslashes"
            )
        if not _FINGERPRINT.fullmatch(self.fingerprint):
            raise ValueError("a request fingerprint is a SHA-256 in lower-case hex")


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """What the first request with a key created, kept until ``expires_at``."""

    tenant_id: uuid.UUID
    actor: Actor
    """The caller who sent the key: keys are never shared between callers."""
    key: str
    fingerprint: str
    resource_type: AuditResourceType
    resource_id: uuid.UUID
    created_at: datetime
    expires_at: datetime

    @classmethod
    def first(
        cls,
        request: IdempotentRequest,
        *,
        tenant_id: uuid.UUID,
        actor: Actor,
        resource_type: AuditResourceType,
        resource_id: uuid.UUID,
        now: datetime,
    ) -> IdempotencyRecord:
        """What ``request``, the first with its key, created at ``now``."""
        return cls(
            tenant_id=tenant_id,
            actor=actor,
            key=request.key,
            fingerprint=request.fingerprint,
            resource_type=resource_type,
            resource_id=resource_id,
            created_at=now,
            expires_at=now + KEY_LIFETIME,
        )

    def has_expired(self, now: datetime) -> bool:
        """After expiry the key is free again: a request with it runs as a new one."""
        return now >= self.expires_at

    def replay(self, request: IdempotentRequest) -> uuid.UUID:
        """The resource the first request created, if ``request`` is that same request again."""
        if request.key != self.key:
            raise ValueError("the request carries another Idempotency-Key")
        if request.fingerprint != self.fingerprint:
            raise IdempotencyKeyReusedError(
                "this Idempotency-Key was already used for a different request; use a new key"
            )
        return self.resource_id
