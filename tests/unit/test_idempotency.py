"""Idempotency keys (ADR-0022): which keys are valid, what a record replays, when it expires, and
the fake's rules, which the adapter's integration tests pin down for PostgreSQL."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditResourceType
from pricewright.domain.errors import ConflictError, RuleViolationError
from pricewright.domain.idempotency import (
    KEY_LIFETIME,
    MAX_KEY_LENGTH,
    IdempotencyKeyInUseError,
    IdempotencyKeyReusedError,
    IdempotencyRecord,
    IdempotentRequest,
    InvalidIdempotencyKeyError,
)
from tests.fakes import FakeUnitOfWork, InMemoryDatabase

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
TENANT = uuid.uuid7()
REP = Actor.person(uuid.uuid7())
KEY = str(uuid.uuid4())  # generated: gitleaks flags literal keys
VISIBLE_ASCII = [chr(code) for code in range(0x21, 0x7F) if chr(code) not in {'"', chr(92)}]


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def request(key: str = KEY, body: str = "{}") -> IdempotentRequest:
    return IdempotentRequest(key, fingerprint(body))


def record(
    sent: IdempotentRequest | None = None, *, actor: Actor = REP, now: datetime = NOW
) -> IdempotencyRecord:
    return IdempotencyRecord.first(
        sent or request(),
        tenant_id=TENANT,
        actor=actor,
        resource_type=AuditResourceType.QUOTE,
        resource_id=uuid.uuid7(),
        now=now,
    )


@given(st.text(alphabet=VISIBLE_ASCII, min_size=1, max_size=MAX_KEY_LENGTH))
def test_idempotent_request_accepts_visible_ascii_keys_up_to_255_characters(key: str) -> None:
    assert IdempotentRequest(key, fingerprint("{}")).key == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "k" * (MAX_KEY_LENGTH + 1),
        "with space",
        'with"quote',
        "with" + chr(92),
        "tab\t",
        "ñandú",
    ],
)
def test_idempotent_request_refuses_empty_long_or_unprintable_keys(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKeyError, match="visible ASCII"):
        IdempotentRequest(key, fingerprint("{}"))


@pytest.mark.parametrize("digest", ["", "abc", fingerprint("{}").upper(), fingerprint("{}") + "0"])
def test_idempotent_request_needs_a_lower_case_sha256_fingerprint(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        IdempotentRequest("key-1", digest)


def test_idempotency_errors_map_to_409_and_422() -> None:
    assert issubclass(IdempotencyKeyInUseError, ConflictError)
    assert issubclass(IdempotencyKeyReusedError, RuleViolationError)
    assert issubclass(InvalidIdempotencyKeyError, RuleViolationError)


def test_idempotency_record_keeps_the_key_for_24_hours() -> None:
    first = record()

    assert first.expires_at - first.created_at == KEY_LIFETIME == timedelta(hours=24)
    assert not first.has_expired(NOW + KEY_LIFETIME - timedelta(microseconds=1))
    assert first.has_expired(NOW + KEY_LIFETIME)


@given(st.sampled_from(["{}", '{"a":1}', '{"a":2}']), st.sampled_from(["{}", '{"a":1}']))
def test_idempotency_record_replays_only_the_same_request(first_body: str, retry_body: str) -> None:
    first = record(request(body=first_body))
    retry = request(body=retry_body)

    if first_body == retry_body:
        assert first.replay(retry) == first.resource_id
    else:
        with pytest.raises(IdempotencyKeyReusedError, match="different request"):
            first.replay(retry)


def test_idempotency_record_never_replays_another_key() -> None:
    with pytest.raises(ValueError, match="another Idempotency-Key"):
        record(request("key-1")).replay(request("key-2"))


# The fake follows the adapter: a key is held from its first use until the unit of work ends.


async def test_fake_idempotency_keys_are_held_until_the_unit_of_work_ends() -> None:
    database = InMemoryDatabase()

    async with FakeUnitOfWork(database) as first:
        first.bind_tenant(TENANT)
        assert await first.idempotency_keys.claim(REP, "key-1", now=NOW) is None
        async with FakeUnitOfWork(database) as second:
            second.bind_tenant(TENANT)
            with pytest.raises(IdempotencyKeyInUseError, match="in progress"):
                await second.idempotency_keys.claim(REP, "key-1", now=NOW)
            with pytest.raises(IdempotencyKeyInUseError):
                await second.idempotency_keys.add(record(request("key-1")))
    async with FakeUnitOfWork(database) as third:
        third.bind_tenant(TENANT)

        assert await third.idempotency_keys.claim(REP, "key-1", now=NOW) is None


async def test_fake_idempotency_keys_remember_only_committed_records() -> None:
    database = InMemoryDatabase()
    kept, dropped = record(request("kept")), record(request("dropped"))

    for stored, commit in ((kept, True), (dropped, False)):
        async with FakeUnitOfWork(database) as uow:
            uow.bind_tenant(TENANT)
            await uow.idempotency_keys.add(stored)
            if commit:
                await uow.commit()

    async with FakeUnitOfWork(database) as uow:
        uow.bind_tenant(TENANT)
        assert await uow.idempotency_keys.claim(REP, "kept", now=NOW) == kept
        assert await uow.idempotency_keys.claim(REP, "dropped", now=NOW) is None


async def test_fake_idempotency_keys_belong_to_their_caller_and_tenant() -> None:
    database = InMemoryDatabase()
    async with FakeUnitOfWork(database) as uow:
        uow.bind_tenant(TENANT)
        await uow.idempotency_keys.add(record())
        await uow.commit()

    async with FakeUnitOfWork(database) as other_caller:
        other_caller.bind_tenant(TENANT)
        found = await other_caller.idempotency_keys.claim(
            Actor.person(uuid.uuid7()), request().key, now=NOW
        )
        assert found is None
    async with FakeUnitOfWork(database) as other_tenant:
        other_tenant.bind_tenant(uuid.uuid7())
        assert await other_tenant.idempotency_keys.claim(REP, request().key, now=NOW) is None
        with pytest.raises(RuntimeError, match="unit of work's tenant"):
            await other_tenant.idempotency_keys.add(record())


async def test_fake_idempotency_keys_replace_only_expired_records() -> None:
    database = InMemoryDatabase()
    first = record()
    async with FakeUnitOfWork(database) as uow:
        uow.bind_tenant(TENANT)
        await uow.idempotency_keys.add(first)
        await uow.commit()
    later = NOW + KEY_LIFETIME
    renewed = record(now=later)  # the same key, a day later

    async with FakeUnitOfWork(database) as uow:
        uow.bind_tenant(TENANT)
        with pytest.raises(RuntimeError, match="unexpired"):
            await uow.idempotency_keys.add(record(now=later - timedelta(seconds=1)))
        assert await uow.idempotency_keys.claim(REP, first.key, now=later) is None
        await uow.idempotency_keys.add(renewed)
        await uow.commit()

    async with FakeUnitOfWork(database) as uow:
        uow.bind_tenant(TENANT)
        assert await uow.idempotency_keys.claim(REP, first.key, now=later) == renewed
