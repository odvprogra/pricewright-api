import uuid

import pytest

from pricewright.domain.auth import (
    GRANTABLE_SCOPES,
    PEOPLE_ONLY_PERMISSIONS,
    ROLE_PERMISSIONS,
    Permission,
    PermissionDeniedError,
    Principal,
)
from pricewright.domain.users import Role


def principal(role: Role) -> Principal:
    return Principal(tenant_id=uuid.uuid7(), subject_id=uuid.uuid7(), role=role)


def test_every_role_has_a_permission_set() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_admins_hold_every_permission() -> None:
    assert principal(Role.ADMIN).permissions == frozenset(Permission)


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
def test_reps_and_managers_read_but_do_not_manage_the_tenant(role: Role) -> None:
    caller = principal(role)

    caller.require(Permission.TENANT_READ)
    with pytest.raises(PermissionDeniedError, match="tenant:manage"):
        caller.require(Permission.TENANT_MANAGE)


def test_service_accounts_hold_their_scopes() -> None:
    caller = Principal(uuid.uuid7(), uuid.uuid7(), scopes=frozenset({Permission.TENANT_READ}))

    assert caller.is_service_account
    caller.require(Permission.TENANT_READ)


def test_service_accounts_never_get_people_only_permissions() -> None:
    caller = Principal(uuid.uuid7(), uuid.uuid7(), scopes=frozenset(Permission))

    assert caller.permissions == GRANTABLE_SCOPES
    for permission in PEOPLE_ONLY_PERMISSIONS:
        with pytest.raises(PermissionDeniedError):
            caller.require(permission)


@pytest.mark.parametrize("role", [Role.SALES_REP, Role.SALES_MANAGER])
def test_reps_and_managers_read_but_do_not_manage_the_catalog(role: Role) -> None:
    caller = principal(role)

    caller.require(Permission.CATALOG_READ)
    with pytest.raises(PermissionDeniedError, match="catalog:manage"):
        caller.require(Permission.CATALOG_MANAGE)


def test_integrations_can_read_the_catalog_but_never_change_it() -> None:
    assert Permission.CATALOG_READ in GRANTABLE_SCOPES
    assert Permission.CATALOG_MANAGE not in GRANTABLE_SCOPES


@pytest.mark.parametrize("role", list(Role))
def test_every_role_manages_customers(role: Role) -> None:
    caller = principal(role)

    caller.require(Permission.CUSTOMERS_READ)
    caller.require(Permission.CUSTOMERS_MANAGE)


def test_integrations_can_be_granted_customer_access() -> None:
    assert {Permission.CUSTOMERS_READ, Permission.CUSTOMERS_MANAGE} <= GRANTABLE_SCOPES


@pytest.mark.parametrize("role", list(Role))
def test_every_role_reads_costs(role: Role) -> None:
    assert principal(role).holds(Permission.COSTS_READ)


def test_integrations_never_read_costs() -> None:
    caller = Principal(uuid.uuid7(), uuid.uuid7(), scopes=frozenset(Permission))

    assert Permission.COSTS_READ not in GRANTABLE_SCOPES
    assert not caller.holds(Permission.COSTS_READ)


def test_everyone_reads_pricing_rules_but_only_managers_and_admins_manage_them() -> None:
    assert all(principal(role).holds(Permission.PRICING_READ) for role in Role)
    assert principal(Role.SALES_MANAGER).holds(Permission.PRICING_MANAGE)
    assert principal(Role.ADMIN).holds(Permission.PRICING_MANAGE)
    with pytest.raises(PermissionDeniedError, match="pricing:manage"):
        principal(Role.SALES_REP).require(Permission.PRICING_MANAGE)


@pytest.mark.parametrize("role", list(Role))
def test_every_role_reads_and_builds_quotes(role: Role) -> None:
    caller = principal(role)

    caller.require(Permission.QUOTES_READ)
    caller.require(Permission.QUOTES_MANAGE)


def test_integrations_can_be_granted_quote_drafting() -> None:
    # erp-mcp-server: list_quotes, get_quote, create_draft_quote, add / remove lines, submit_quote.
    assert {Permission.QUOTES_READ, Permission.QUOTES_MANAGE} <= GRANTABLE_SCOPES


def test_only_managers_and_admins_override_prices_and_never_integrations() -> None:
    assert principal(Role.SALES_MANAGER).holds(Permission.QUOTES_OVERRIDE)
    assert principal(Role.ADMIN).holds(Permission.QUOTES_OVERRIDE)
    with pytest.raises(PermissionDeniedError, match="quotes:override"):
        principal(Role.SALES_REP).require(Permission.QUOTES_OVERRIDE)
    assert Permission.QUOTES_OVERRIDE not in GRANTABLE_SCOPES


def test_integrations_can_read_pricing_but_never_change_it() -> None:
    assert Permission.PRICING_READ in GRANTABLE_SCOPES
    assert Permission.PRICING_MANAGE not in GRANTABLE_SCOPES
