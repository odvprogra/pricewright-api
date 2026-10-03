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


def normalize_password(raw: str) -> str:
    """NFKC-normalize (NIST SP 800-63B-4 §3.1.1.2) and enforce the length policy."""
    password = unicodedata.normalize("NFKC", raw)
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
        full_name = full_name.strip()
        if not full_name or len(full_name) > MAX_NAME_LENGTH:
            raise InvalidUserError(f"full_name must have 1 to {MAX_NAME_LENGTH} characters")
        return cls(
            id=uuid.uuid7(),
            tenant_id=tenant_id,
            email=normalize_email(email),
            full_name=full_name,
            role=role,
            password_hash=password_hash,
        )
