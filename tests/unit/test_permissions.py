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
