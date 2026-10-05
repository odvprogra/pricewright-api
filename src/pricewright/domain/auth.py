"""Who is calling and what they may do (ADR-0007).

Authorization is by permission, never by role name: a role is a named set of permissions, and every
use case requires one permission. Service accounts hold a subset of the same permissions as scopes,
so the same checks cover them.
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
    SERVICE_ACCOUNTS_MANAGE = "service_accounts:manage"
    AUDIT_READ = "audit:read"
    CATALOG_READ = "catalog:read"
    CATALOG_MANAGE = "catalog:manage"
    CUSTOMERS_READ = "customers:read"
    CUSTOMERS_MANAGE = "customers:manage"
    COSTS_READ = "costs:read"
    """Unit costs and the margins they reveal (ADR-0017)."""
    PRICING_READ = "pricing:read"
    """The pricing rules, and prices computed with them."""
    PRICING_MANAGE = "pricing:manage"
    QUOTES_READ = "quotes:read"
    QUOTES_MANAGE = "quotes:manage"
    """Create and edit drafts and their lines, submit, recall, cancel and revise quotes."""
    QUOTES_SEND = "quotes:send"
    """Send a quote to the customer and record the customer's acceptance: commitments."""
    QUOTES_OVERRIDE = "quotes:override"
    """Set or clear a manual price override on a quote line (brief §4, rule 1)."""


_SELLING = frozenset(
    {
        Permission.TENANT_READ,
        Permission.CATALOG_READ,
        Permission.COSTS_READ,
        Permission.CUSTOMERS_READ,
        Permission.CUSTOMERS_MANAGE,
        Permission.PRICING_READ,
        Permission.QUOTES_READ,
        Permission.QUOTES_MANAGE,
        Permission.QUOTES_SEND,
    }
)

# Brief §2: everyone sees the tenant's settings, the catalog with its costs (reps see margins on
# quote lines, brief §3) and the pricing rules, and manages customers and quotes; managers also
# manage the pricing rules and override prices on quote lines; only admins change the settings
# and the catalog, and manage users.
ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    Role.SALES_REP: _SELLING,
    Role.SALES_MANAGER: _SELLING | {Permission.PRICING_MANAGE, Permission.QUOTES_OVERRIDE},
    Role.ADMIN: frozenset(Permission),
}


# Some permissions stay with people, whatever a service account is granted: administering the
# tenant, its users and its service accounts; reading the audit trail of what they do; changing the
# catalog or the pricing rules, or overriding a quote's prices, so a person decides every price
# change (decision D-02); sending quotes and recording their acceptance, which commit the company
# to a customer; and reading costs, which an integration such as an LLM drafting customer messages
# could leak (ADR-0017, OWASP LLM06 excessive agency).
PEOPLE_ONLY_PERMISSIONS = frozenset(
    {
        Permission.TENANT_MANAGE,
        Permission.USERS_MANAGE,
        Permission.SERVICE_ACCOUNTS_MANAGE,
        Permission.AUDIT_READ,
        Permission.CATALOG_MANAGE,
        Permission.COSTS_READ,
        Permission.PRICING_MANAGE,
        Permission.QUOTES_SEND,
        Permission.QUOTES_OVERRIDE,
    }
)
GRANTABLE_SCOPES = frozenset(Permission) - PEOPLE_ONLY_PERMISSIONS


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling: a user (with a role) or a service account (with scopes)."""

    tenant_id: uuid.UUID
    subject_id: uuid.UUID
    """The user's or the service account's id."""
    role: Role | None = None
    scopes: frozenset[Permission] = frozenset()

    @property
    def is_service_account(self) -> bool:
        return self.role is None

    @property
    def permissions(self) -> frozenset[Permission]:
        if self.role is None:
            return self.scopes & GRANTABLE_SCOPES
        return ROLE_PERMISSIONS[self.role]

    def holds(self, permission: Permission) -> bool:
        """For what a response shows rather than whether a request runs, such as costs."""
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        if not self.holds(permission):
            raise PermissionDeniedError(f"this action needs the {permission} permission")
