"""Who is calling: the authenticated principal, and the error for anyone who is not (ADR-0007)."""

import uuid
from dataclasses import dataclass
from typing import ClassVar

from pricewright.domain.errors import DomainError
from pricewright.domain.users import Role


class AuthenticationError(DomainError):
    """Missing or invalid credentials. Deliberately vague: it never says which part was wrong."""

    code: ClassVar[str] = "authentication_failed"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated user, as carried by an access token."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
