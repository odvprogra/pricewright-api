"""Service accounts and their API keys: how other systems call the API (ADR-0007).

``erp-mcp-server`` and ``ops-copilot`` act for a tenant through a service account, which holds a
subset of the non-administrative permissions as scopes.

Keys look like ``pwk_`` + 40 random base62 characters + a 6-character CRC32 checksum, after GitHub's
token format: the prefix lets secret scanners spot a leaked key, and the checksum rejects a
mistyped or made-up key without a database query. Only a SHA-256 digest is stored, and the key is
shown once, when issued.
"""

import secrets
import string
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewright.domain.auth import GRANTABLE_SCOPES, Permission
from pricewright.domain.digests import digest
from pricewright.domain.errors import ConflictError, RuleViolationError

KEY_PREFIX = "pwk_"
RANDOM_LENGTH = 40  # 238 bits of base62
CHECKSUM_LENGTH = 6
KEY_LENGTH = len(KEY_PREFIX) + RANDOM_LENGTH + CHECKSUM_LENGTH
HINT_LENGTH = len(KEY_PREFIX) + 4
# Two active keys let a client rotate without downtime; AWS IAM allows two access keys per user.
MAX_ACTIVE_KEYS = 2
# Recording every use would write on every request; once an hour is precise enough to spot unused
# keys.
LAST_USED_PRECISION = timedelta(hours=1)
MAX_NAME_LENGTH = 100
_BASE62 = string.digits + string.ascii_letters


class InvalidServiceAccountError(RuleViolationError):
    code = "invalid_service_account"


class TooManyApiKeysError(ConflictError):
    code = "too_many_api_keys"


def _base62(number: int, width: int) -> str:
    characters = []
    for _ in range(width):
        number, remainder = divmod(number, len(_BASE62))
        characters.append(_BASE62[remainder])
    return "".join(reversed(characters))


def _checksum(random_part: str) -> str:
    return _base62(zlib.crc32(random_part.encode()), CHECKSUM_LENGTH)


def new_api_key() -> str:
    random_part = "".join(secrets.choice(_BASE62) for _ in range(RANDOM_LENGTH))
    return KEY_PREFIX + random_part + _checksum(random_part)


def is_well_formed(key: str) -> bool:
    """Prefix, length, alphabet and checksum: a cheap filter before any lookup."""
    if len(key) != KEY_LENGTH or not key.startswith(KEY_PREFIX):
        return False
    body = key.removeprefix(KEY_PREFIX)
    random_part, checksum = body[:RANDOM_LENGTH], body[RANDOM_LENGTH:]
    return all(char in _BASE62 for char in body) and checksum == _checksum(random_part)


@dataclass(slots=True)
class ServiceAccount:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    scopes: frozenset[Permission]
    is_active: bool = True

    @classmethod
    def create(
        cls, *, tenant_id: uuid.UUID, name: str, scopes: frozenset[Permission]
    ) -> ServiceAccount:
        name = name.strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            raise InvalidServiceAccountError(f"name must have 1 to {MAX_NAME_LENGTH} characters")
        if forbidden := scopes - GRANTABLE_SCOPES:
            listed = ", ".join(sorted(forbidden))
            raise InvalidServiceAccountError(f"service accounts cannot be granted {listed}")
        return cls(id=uuid.uuid7(), tenant_id=tenant_id, name=name, scopes=scopes)


@dataclass(slots=True)
class ApiKey:
    id: uuid.UUID
    tenant_id: uuid.UUID
    service_account_id: uuid.UUID
    key_digest: str
    hint: str
    """The key's first characters (``pwk_Ab3x``), to tell keys apart without revealing them."""
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @classmethod
    def issue(
        cls,
        account: ServiceAccount,
        *,
        key: str,
        now: datetime,
        expires_at: datetime | None = None,
    ) -> ApiKey:
        if expires_at is not None and expires_at <= now:
            raise InvalidServiceAccountError("expires_at must be in the future")
        return cls(
            id=uuid.uuid7(),
            tenant_id=account.tenant_id,
            service_account_id=account.id,
            key_digest=digest(key),
            hint=key[:HINT_LENGTH],
            created_at=now,
            expires_at=expires_at,
        )

    def is_usable(self, now: datetime) -> bool:
        return self.revoked_at is None and (self.expires_at is None or now < self.expires_at)

    def record_use(self, now: datetime) -> bool:
        """Note the use, at most once per ``LAST_USED_PRECISION``. True if something changed."""
        if self.last_used_at is not None and now - self.last_used_at < LAST_USED_PRECISION:
            return False
        self.last_used_at = now
        return True

    def revoke(self, now: datetime) -> None:
        if self.revoked_at is None:
            self.revoked_at = now
