import uuid
from datetime import UTC, datetime, timedelta

from pricewright.domain.sessions import (
    ABSOLUTE_LIFETIME,
    IDLE_LIFETIME,
    REFRESH_PREFIX,
    RefreshToken,
    digest,
    new_refresh_token,
)

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def family() -> RefreshToken:
    return RefreshToken.start_family(
        tenant_id=uuid.uuid7(), user_id=uuid.uuid7(), token="pwr_first", now=NOW
    )


def test_new_refresh_tokens_are_prefixed_and_unique() -> None:
    first, second = new_refresh_token(), new_refresh_token()

    assert first.startswith(REFRESH_PREFIX)
    assert first != second
    assert len(first) > 40  # 256 random bits


def test_refresh_tokens_are_stored_as_sha256_digests() -> None:
    token = family()

    assert token.token_digest == digest("pwr_first")
    assert len(token.token_digest) == 64
    assert "pwr_first" not in repr(token)


def test_a_family_expires_after_the_idle_lifetime_within_the_absolute_one() -> None:
    token = family()

    assert token.expires_at == NOW + IDLE_LIFETIME
    assert token.family_expires_at == NOW + ABSOLUTE_LIFETIME


def test_a_successor_extends_the_idle_lifetime_but_never_the_family() -> None:
    token = family()

    early = token.successor(token="pwr_second", now=NOW + timedelta(days=1))
    late = token.successor(token="pwr_third", now=NOW + timedelta(days=25))

    assert early.expires_at == NOW + timedelta(days=1) + IDLE_LIFETIME
    assert late.expires_at == token.family_expires_at
    assert {early.family_id, late.family_id} == {token.family_id}
    assert early.id != token.id


def test_a_token_is_usable_only_until_used_revoked_or_expired() -> None:
    fresh, used, revoked = family(), family(), family()
    used.used_at = NOW
    revoked.revoked_at = NOW

    assert fresh.is_usable(NOW)
    assert not fresh.is_usable(fresh.expires_at)
    assert not used.is_usable(NOW)
    assert used.was_used
    assert not revoked.is_usable(NOW)
