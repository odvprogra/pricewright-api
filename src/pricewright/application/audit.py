"""What the audit trail keeps of each record (ADR-0013): business fields, never secrets or digests.

Values are JSON scalars: decimals and times as strings, scopes space-delimited as in OAuth
(RFC 6749 §3.3). Decimals keep their column's scale, so 12.5 and 12.50 are the same value and an
edit between them is no change.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import AuditEventFilter, UnitOfWork, UnitOfWorkFactory
from pricewright.domain.audit import AuditAction, AuditEvent, AuditValue, Changes
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.catalog import Product, ProductCategory
from pricewright.domain.customers import Customer
from pricewright.domain.money import AMOUNT_DECIMAL_PLACES
from pricewright.domain.pricing_rules import RATE_DECIMAL_PLACES as RULE_RATE_PLACES
from pricewright.domain.pricing_rules import PricingRule
from pricewright.domain.quantities import QUANTITY_DECIMAL_PLACES
from pricewright.domain.service_accounts import ApiKey, ServiceAccount
from pricewright.domain.tenants import RATE_DECIMAL_PLACES, Tenant
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


def _decimal(value: Decimal, places: int) -> str:
    """As the column stores it: ``Decimal("12.5")`` with 4 places is ``"12.5000"``."""
    return str(value.quantize(Decimal(1).scaleb(-places)))


def tenant_fields(tenant: Tenant) -> dict[str, AuditValue]:
    return {
        "name": tenant.name,
        "tax_rate": _decimal(tenant.settings.tax_rate, RATE_DECIMAL_PLACES),
        "approval_threshold": _decimal(tenant.settings.approval_threshold, RATE_DECIMAL_PLACES),
        "quote_prefix": tenant.settings.quote_prefix,
        "quote_validity_days": tenant.settings.quote_validity_days,
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


def category_fields(category: ProductCategory) -> dict[str, AuditValue]:
    return {"name": category.name}


def product_fields(product: Product) -> dict[str, AuditValue]:
    """Amounts as decimal strings; the currency is the tenant's and never changes."""
    return {
        "sku": product.sku,
        "name": product.name,
        "category_id": None if product.category_id is None else str(product.category_id),
        "unit": product.unit.value,
        "list_price": _decimal(product.list_price.amount, AMOUNT_DECIMAL_PLACES),
        "unit_cost": _decimal(product.unit_cost.amount, AMOUNT_DECIMAL_PLACES),
        "is_active": product.is_active,
    }


def customer_fields(customer: Customer) -> dict[str, AuditValue]:
    return {
        "account_number": customer.account_number,
        "name": customer.name,
        "tax_id": customer.tax_id,
        "tier": customer.tier.value,
        "payment_terms_days": customer.payment_terms_days,
        "is_active": customer.is_active,
    }


def _id(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def pricing_rule_fields(rule: PricingRule) -> dict[str, AuditValue]:
    """Brackets as ``min_quantity:rate`` pairs, space-delimited; none for other kinds."""
    brackets = " ".join(
        f"{_decimal(b.min_quantity, QUANTITY_DECIMAL_PLACES)}:{_decimal(b.rate, RULE_RATE_PLACES)}"
        for b in rule.brackets
    )
    return {
        "kind": rule.kind.value,
        "name": rule.name,
        "product_id": _id(rule.product_id),
        "category_id": _id(rule.category_id),
        "customer_tier": None if rule.customer_tier is None else rule.customer_tier.value,
        "rate": None if rule.rate is None else _decimal(rule.rate, RULE_RATE_PLACES),
        "brackets": brackets or None,
        "valid_from": _time(rule.valid_from),
        "valid_to": _time(rule.valid_to),
        "is_active": rule.is_active,
    }
