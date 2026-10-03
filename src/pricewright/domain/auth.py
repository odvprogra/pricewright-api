"""Who is calling and what they may do (ADR-0007).

Authorization is by permission, never by role name: a role is a named set of permissions, and every
use case requires one permission. Service accounts (later in M1) hold a subset of the same
permissions as scopes, so the same checks cover them.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from pricewright.domain.errors import DomainError
from pricewright.domain.users import Role


class AuthenticationError(DomainError):
    """Missing or invalid credentials. Deliberately vague: it never says which part was wrong."""

    code: ClassVar[str] = "authentication_failed"


class PermissionDeniedError(DomainError):
    code: ClassVar[str] = "permission_denied"


class Permission(StrEnum):
    """``resource:action``. New permissions arrive with the features that need them."""

    TENANT_READ = "tenant:read"
    TENANT_MANAGE = "tenant:manage"
    USERS_MANAGE = "users:manage"


# Brief §2: reps and managers see the tenant's settings; only admins change them and manage users.
ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    Role.SALES_REP: frozenset({Permission.TENANT_READ}),
    Role.SALES_MANAGER: frozenset({Permission.TENANT_READ}),
    Role.ADMIN: frozenset(Permission),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated user, as carried by an access token."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: Role

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def require(self, permission: Permission) -> None:
        if permission not in self.permissions:
            raise PermissionDeniedError(f"this action needs the {permission} permission")
