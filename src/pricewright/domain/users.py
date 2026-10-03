"""Users: the people of a tenant who sign in with an email and a password (ADR-0007)."""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from enum import StrEnum

from pricewright.domain.errors import ConflictError, RuleViolationError

# NIST SP 800-63B-4 §3.1.1.2: at least 15 characters when the password is the only factor, a maximum
# of at least 64, and no composition rules.
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128
MAX_EMAIL_LENGTH = 254  # RFC 5321 path limit
MAX_NAME_LENGTH = 200
# NIST SP 800-63B-4 §3.2.2: no more than 100 consecutive failed attempts before disabling the
# password; an admin unlocks the account.
MAX_FAILED_LOGINS = 100
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Role(StrEnum):
    SALES_REP = "sales_rep"
    SALES_MANAGER = "sales_manager"
    ADMIN = "admin"


class InvalidUserError(RuleViolationError):
    code = "invalid_user"


class WeakPasswordError(RuleViolationError):
    code = "weak_password"


class EmailAlreadyRegisteredError(ConflictError):
    code = "email_already_registered"


def normalize_email(raw: str) -> str:
    """Emails identify users across every tenant, compared case-insensitively."""
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not _EMAIL.fullmatch(email):
        raise InvalidUserError("email must be a valid address")
    return email


def normalize_full_name(raw: str) -> str:
    full_name = raw.strip()
    if not full_name or len(full_name) > MAX_NAME_LENGTH:
        raise InvalidUserError(f"full_name must have 1 to {MAX_NAME_LENGTH} characters")
    return full_name


def canonical_password(raw: str) -> str:
    """NFKC-normalize (NIST SP 800-63B-4 §3.1.1.2), so equivalent input always hashes the same."""
    return unicodedata.normalize("NFKC", raw)


def normalize_password(raw: str) -> str:
    """Canonicalize a new password and enforce the length policy."""
    password = canonical_password(raw)
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"password must have {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters"
        )
    return password


@dataclass(slots=True)
class User:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    password_hash: str
    is_active: bool = True
    failed_login_attempts: int = 0

    @property
    def is_locked(self) -> bool:
        return self.failed_login_attempts >= MAX_FAILED_LOGINS

    @property
    def can_sign_in(self) -> bool:
        return self.is_active and not self.is_locked

    def record_failed_login(self) -> None:
        self.failed_login_attempts += 1

    def record_successful_login(self) -> None:
        self.failed_login_attempts = 0

    @classmethod
    def create(
        cls,
        *,
        tenant_id: uuid.UUID,
        email: str,
        full_name: str,
        role: Role,
        password_hash: str,
    ) -> User:
        return cls(
            id=uuid.uuid7(),
            tenant_id=tenant_id,
            email=normalize_email(email),
            full_name=normalize_full_name(full_name),
            role=role,
            password_hash=password_hash,
        )
