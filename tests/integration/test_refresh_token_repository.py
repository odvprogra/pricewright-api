"""Refresh tokens in PostgreSQL: atomic claims, family revocation and tenant-safe keys."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from pricewright.domain.digests import digest
from pricewright.domain.sessions import RefreshToken
from pricewright.domain.tenants import Tenant
from pricewright.domain.users import User
from pricewright.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.integration.data import Sessions, register

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


async def start_session(sessions: Sessions, tenant: Tenant, user: User) -> RefreshToken:
    token = RefreshToken.start_family(
        tenant_id=tenant.id, user_id=user.id, token=f"pwr_{uuid.uuid4().hex}", now=NOW
    )
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        uow.bind_tenant(tenant.id)
        await uow.refresh_tokens.add(token)
        await uow.commit()
    return token


async def test_identity_lookup_finds_a_refresh_token_by_digest(session_factory: Sessions) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    token = await start_session(session_factory, northfield, avery)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.identities.refresh_token_by_digest(token.token_digest) == token
        assert await uow.identities.refresh_token_by_digest(digest("pwr_unknown")) is None


async def test_claim_succeeds_once(session_factory: Sessions) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    token = await start_session(session_factory, northfield, avery)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        first = await uow.refresh_tokens.claim(token.id, NOW)
        second = await uow.refresh_tokens.claim(token.id, NOW)

    assert (first, second) == (True, False)


async def test_concurrent_claims_of_the_same_token_let_exactly_one_win(
    session_factory: Sessions,
) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    token = await start_session(session_factory, northfield, avery)

    async def claim_and_commit() -> bool:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.bind_tenant(northfield.id)
            claimed = await uow.refresh_tokens.claim(token.id, NOW)
            await uow.commit()
            return claimed

    results = await asyncio.gather(claim_and_commit(), claim_and_commit())

    assert sorted(results) == [False, True]


async def test_revoke_family_revokes_only_that_family_of_that_tenant(
    session_factory: Sessions,
) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    revoked = await start_session(session_factory, northfield, avery)
    other_family = await start_session(session_factory, northfield, avery)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(northfield.id)
        await uow.refresh_tokens.revoke_family(revoked.family_id, NOW)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        stored = await uow.identities.refresh_token_by_digest(revoked.token_digest)
        untouched = await uow.identities.refresh_token_by_digest(other_family.token_digest)
    assert stored is not None
    assert stored.revoked_at == NOW
    assert untouched == other_family


async def test_another_tenant_cannot_claim_or_revoke_a_session(session_factory: Sessions) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    larkspur, _ = await register(session_factory, "Larkspur")
    token = await start_session(session_factory, northfield, avery)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        claimed = await uow.refresh_tokens.claim(token.id, NOW)
        await uow.refresh_tokens.revoke_family(token.family_id, NOW)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert await uow.identities.refresh_token_by_digest(token.token_digest) == token
    assert not claimed


async def test_database_rejects_a_session_whose_user_belongs_to_another_tenant(
    session_factory: Sessions,
) -> None:
    _, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    larkspur, _ = await register(session_factory, "Larkspur")
    # The application never builds this; the composite foreign key refuses it anyway.
    crossed = RefreshToken.start_family(
        tenant_id=larkspur.id, user_id=avery.id, token="pwr_crossed", now=NOW
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)
        await uow.refresh_tokens.add(crossed)

        with pytest.raises(IntegrityError, match="fk_refresh_tokens_tenant_id_user_id_users"):
            await uow.commit()


async def test_refresh_token_repository_refuses_a_token_of_another_tenant(
    session_factory: Sessions,
) -> None:
    northfield, [avery] = await register(session_factory, "Northfield", "avery@northfield.example")
    larkspur, _ = await register(session_factory, "Larkspur")
    token = RefreshToken.start_family(
        tenant_id=northfield.id, user_id=avery.id, token="pwr_other", now=NOW
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.bind_tenant(larkspur.id)

        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await uow.refresh_tokens.add(token)
