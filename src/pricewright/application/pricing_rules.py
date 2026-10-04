"""Pricing rules: everyone reads them; managers and admins maintain them (brief §2, ADR-0018)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pricewright.application.audit import pricing_rule_fields, record
from pricewright.application.catalog import ensure_category_exists, ensure_product_exists
from pricewright.application.pagination import Keyset, Page, page_of
from pricewright.application.ports import (
    Clock,
    PricingRuleQuery,
    PricingRuleSort,
    UnitOfWorkFactory,
)
from pricewright.domain.audit import AuditAction, changed, created
from pricewright.domain.auth import Permission, Principal
from pricewright.domain.customers import CustomerTier
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.pricing_rules import Bracket, PricingRule, RuleKind
from pricewright.domain.updates import KEEP, Keep


@dataclass(frozen=True, slots=True)
class NewPricingRule:
    kind: RuleKind
    name: str
    rate: Decimal | None = None
    brackets: Sequence[Bracket] = ()
    product_id: UUID | None = None
    category_id: UUID | None = None
    customer_tier: CustomerTier | None = None
    valid_from: datetime | None = None
    """None: from now."""
    valid_to: datetime | None = None


async def create_pricing_rule(
    principal: Principal, new: NewPricingRule, *, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> PricingRule:
    principal.require(Permission.PRICING_MANAGE)
    now = clock()
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        rule = PricingRule.create(
            tenant_id=principal.tenant_id,
            kind=new.kind,
            name=new.name,
            valid_from=now if new.valid_from is None else new.valid_from,
            valid_to=new.valid_to,
            rate=new.rate,
            brackets=new.brackets,
            product_id=new.product_id,
            category_id=new.category_id,
            customer_tier=new.customer_tier,
        )
        await ensure_product_exists(uow, rule.product_id)
        await ensure_category_exists(uow, rule.category_id)
        await uow.pricing_rules.add(rule)
        changes = created(pricing_rule_fields(rule))
        await record(uow, principal, AuditAction.PRICING_RULE_CREATED, rule.id, changes, now=now)
        await uow.commit()
    return rule


def _position(rule: PricingRule, sort: PricingRuleSort) -> Keyset:
    return Keyset(rule.id, rule.name if sort is PricingRuleSort.NAME else None)


async def list_pricing_rules(
    principal: Principal,
    query: PricingRuleQuery,
    *,
    after: Keyset | None,
    limit: int,
    unit_of_work: UnitOfWorkFactory,
) -> Page[PricingRule]:
    principal.require(Permission.PRICING_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        rules = await uow.pricing_rules.page(query, after=after, limit=limit + 1)
    return page_of(rules, limit, lambda rule: _position(rule, query.sort))


async def get_pricing_rule(
    principal: Principal, rule_id: UUID, *, unit_of_work: UnitOfWorkFactory
) -> PricingRule:
    principal.require(Permission.PRICING_READ)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        rule = await uow.pricing_rules.get(rule_id)
    if rule is None:
        raise NotFoundError("no such pricing rule")
    return rule


@dataclass(frozen=True, slots=True)
class PricingRuleChanges:
    """Fields left as ``None`` keep their value; ``valid_to`` keeps it with ``KEEP``, and ``None``
    removes the end."""

    name: str | None = None
    rate: Decimal | None = None
    brackets: Sequence[Bracket] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | Keep | None = KEEP
    is_active: bool | None = None


async def change_pricing_rule(
    principal: Principal,
    rule_id: UUID,
    changes: PricingRuleChanges,
    *,
    expected_version: int,
    unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> PricingRule:
    """Edit, end-date or deactivate a rule. Its kind, scope and tier never change."""
    principal.require(Permission.PRICING_MANAGE)
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        rule = await uow.pricing_rules.get(rule_id)
        if rule is None:
            raise NotFoundError("no such pricing rule")
        if rule.version != expected_version:
            raise StaleVersionError("the pricing rule was changed by someone else; reload it")
        before = pricing_rule_fields(rule)
        rule.change(
            name=changes.name,
            rate=changes.rate,
            brackets=changes.brackets,
            valid_from=changes.valid_from,
            valid_to=changes.valid_to,
            is_active=changes.is_active,
        )
        await uow.pricing_rules.save(rule)
        edits = changed(before, pricing_rule_fields(rule))
        await record(uow, principal, AuditAction.PRICING_RULE_UPDATED, rule.id, edits, now=clock())
        await uow.commit()
    return rule
