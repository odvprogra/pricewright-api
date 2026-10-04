"""What the audit trail keeps of each record (ADR-0013): business fields, never secrets or digests.

Values are JSON scalars: decimals and times as strings, scopes space-delimited as in OAuth
(RFC 6749 §3.3).
"""

from datetime import datetime
from uuid import UUID

from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import AuditEventFilter, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.audit import AuditAction, AuditEvent, AuditValue, Changes
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.service_accounts import ApiKey, ServiceAccount
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import User


async def record(
    uow: UnitOfWork,
    principal: Principal,
    action: AuditAction,
    resource_id: UUID,
    changes: Changes,
    *,
    now: datetime,
) -> None:
    """Append the event to the unit of work. An edit that changed nothing is not an event."""
    if changes:
        await uow.audit_events.add(
            AuditEvent.record(principal, action, resource_id, changes, now=now)
        )


async def list_audit_events(
    principal: Principal,
    where: AuditEventFilter,
    *,
    after: Keyset | None,
    limit: int,
    unit_of_work: UnitOfWorkFactory,
) -> Page[AuditEvent]:
    """The tenant's audit trail, newest first (admins only)."""
    principal.require(Permission.AUDIT_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        events = await uow.audit_events.page(
            where, before=None if after is None else after.id, limit=limit + 1
        )
    return page_of(events, limit, lambda event: Keyset(event.id))


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def tenant_fields(tenant: Tenant) -> dict[str, AuditValue]:
    return {
        "name": tenant.name,
        "tax_rate": str(tenant.settings.tax_rate),
        "approval_threshold": str(tenant.settings.approval_threshold),
    }


def user_fields(user: User) -> dict[str, AuditValue]:
    return {
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
        "locked": user.is_locked,
    }


def service_account_fields(account: ServiceAccount) -> dict[str, AuditValue]:
    return {
        "name": account.name,
        "scopes": " ".join(sorted(account.scopes)),
        "is_active": account.is_active,
    }


def api_key_fields(key: ApiKey) -> dict[str, AuditValue]:
    return {
        "service_account_id": str(key.service_account_id),
        "hint": key.hint,
        "expires_at": _time(key.expires_at),
        "revoked_at": _time(key.revoked_at),
    }
