"""The argon2id adapter, with the real library (no infrastructure needed)."""

import argon2

from pricewright.application.ports import PasswordHasher
from pricewright.infrastructure.passwords import Argon2PasswordHasher

PASSWORD = "correct horse battery staple"


async def test_password_hasher_verifies_the_password_it_hashed() -> None:
    hasher: PasswordHasher = Argon2PasswordHasher()  # also checks it satisfies the port

    password_hash = await hasher.hash(PASSWORD)

    assert password_hash.startswith("$argon2id$")
    assert await hasher.verify(password_hash, PASSWORD)


async def test_password_hasher_rejects_a_wrong_password() -> None:
    hasher = Argon2PasswordHasher()

    password_hash = await hasher.hash(PASSWORD)

    assert not await hasher.verify(password_hash, PASSWORD + "!")


async def test_password_hasher_rejects_a_malformed_hash() -> None:
    assert not await Argon2PasswordHasher().verify("not-a-hash", PASSWORD)


async def test_password_hasher_asks_to_rehash_hashes_made_with_weaker_parameters() -> None:
    weak = Argon2PasswordHasher(argon2.PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1))
    current = Argon2PasswordHasher()

    assert current.needs_rehash(await weak.hash(PASSWORD))
    assert not current.needs_rehash(await current.hash(PASSWORD))


async def test_password_hasher_verifies_an_unknown_user_with_comparable_work() -> None:
    hasher = Argon2PasswordHasher()

    await hasher.verify_unknown(PASSWORD)  # first call also creates the reference hash
    await hasher.verify_unknown(PASSWORD)  # later calls reuse it
