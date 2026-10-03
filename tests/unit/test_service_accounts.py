import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pricewright.domain.auth import Permission
from pricewright.domain.digests import digest
from pricewright.domain.service_accounts import (
    KEY_LENGTH,
    KEY_PREFIX,
    LAST_USED_PRECISION,
    ApiKey,
    InvalidServiceAccountError,
    ServiceAccount,
    is_well_formed,
    new_api_key,
)

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def copilot() -> ServiceAccount:
    return ServiceAccount.create(
        tenant_id=uuid.uuid7(), name=" ops-copilot ", scopes=frozenset({Permission.TENANT_READ})
    )


def test_new_api_keys_are_prefixed_unique_and_well_formed() -> None:
    first, second = new_api_key(), new_api_key()

    assert first.startswith(KEY_PREFIX)
    assert len(first) == KEY_LENGTH
    assert first != second
    assert is_well_formed(first)


def test_a_key_with_one_character_changed_fails_the_checksum() -> None:
    key = new_api_key()
    position = len(KEY_PREFIX) + 5
    tampered = key[:position] + ("A" if key[position] != "A" else "B") + key[position + 1 :]

    assert not is_well_formed(tampered)


@pytest.mark.parametrize(
    "key",
    ["", "pwk_short", "pwr_" + "a" * 46, KEY_PREFIX + "-" * 46, "Bearer pwk_x"],
    ids=["empty", "short", "other-prefix", "alphabet", "garbage"],
)
def test_malformed_keys_are_rejected_without_a_lookup(key: str) -> None:
    assert not is_well_formed(key)


def test_service_account_trims_its_name_and_keeps_its_scopes() -> None:
    account = copilot()

    assert account.name == "ops-copilot"
    assert account.scopes == {Permission.TENANT_READ}
    assert account.is_active


@pytest.mark.parametrize(
    "scope",
    [Permission.TENANT_MANAGE, Permission.USERS_MANAGE, Permission.SERVICE_ACCOUNTS_MANAGE],
)
def test_service_accounts_cannot_be_granted_administrative_permissions(scope: Permission) -> None:
    with pytest.raises(InvalidServiceAccountError, match=scope):
        ServiceAccount.create(tenant_id=uuid.uuid7(), name="robot", scopes=frozenset({scope}))


@pytest.mark.parametrize("name", ["", "  ", "x" * 101])
def test_service_account_names_are_bounded(name: str) -> None:
    with pytest.raises(InvalidServiceAccountError, match="name"):
        ServiceAccount.create(tenant_id=uuid.uuid7(), name=name, scopes=frozenset())


def test_issued_keys_store_only_a_digest_and_a_short_hint() -> None:
    account, key = copilot(), new_api_key()

    issued = ApiKey.issue(account, key=key, now=NOW)

    assert issued.key_digest == digest(key)
    assert issued.hint == key[:8]
    assert key not in repr(issued)
    assert (issued.tenant_id, issued.service_account_id) == (account.tenant_id, account.id)


def test_a_key_cannot_be_issued_already_expired() -> None:
    with pytest.raises(InvalidServiceAccountError, match="future"):
        ApiKey.issue(copilot(), key=new_api_key(), now=NOW, expires_at=NOW)


def test_a_key_is_usable_until_revoked_or_expired() -> None:
    account = copilot()
    forever = ApiKey.issue(account, key=new_api_key(), now=NOW)
    expiring = ApiKey.issue(account, key=new_api_key(), now=NOW, expires_at=NOW + timedelta(days=1))

    assert forever.is_usable(NOW + timedelta(days=3650))
    assert expiring.is_usable(NOW)
    assert not expiring.is_usable(NOW + timedelta(days=1))
    forever.revoke(NOW)
    forever.revoke(NOW + timedelta(days=1))  # revoking twice keeps the first time
    assert not forever.is_usable(NOW)
    assert forever.revoked_at == NOW


def test_use_is_recorded_at_most_once_per_hour() -> None:
    key = ApiKey.issue(copilot(), key=new_api_key(), now=NOW)

    first = key.record_use(NOW)
    soon = key.record_use(NOW + LAST_USED_PRECISION - timedelta(seconds=1))
    later = key.record_use(NOW + LAST_USED_PRECISION)

    assert (first, soon, later) == (True, False, True)
    assert key.last_used_at == NOW + LAST_USED_PRECISION
