import uuid

import pytest

from pricewright.domain.auth import (
    ROLE_PERMISSIONS,
    Permission,
    PermissionDeniedError,
    Principal,
)
from pricewright.domain.users import Role


def principal(role: Role) -> Principal:
    return Principal(tenant_id=uuid.uuid7(), user_id=uuid.uuid7(), role=role)


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
