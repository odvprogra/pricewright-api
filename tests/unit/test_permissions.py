import uuid

import pytest

from pricewright.domain.auth import (
    ADMINISTRATIVE_PERMISSIONS,
    GRANTABLE_SCOPES,
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


def test_service_accounts_never_get_administrative_permissions() -> None:
    caller = Principal(uuid.uuid7(), uuid.uuid7(), scopes=frozenset(Permission))

    assert caller.permissions == GRANTABLE_SCOPES
    for permission in ADMINISTRATIVE_PERMISSIONS:
        with pytest.raises(PermissionDeniedError):
            caller.require(permission)
