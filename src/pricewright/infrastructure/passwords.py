"""argon2id password hashing (ADR-0007).

argon2-cffi's defaults follow RFC 9106's second recommended profile (t=3, 64 MiB, p=4), above
OWASP's minimum (t=2, 19 MiB, p=1). Hashing runs in a worker thread so it never blocks the event
loop.
"""

import asyncio
import secrets

import argon2
from argon2.exceptions import InvalidHashError, VerificationError


class Argon2PasswordHasher:
    def __init__(self, hasher: argon2.PasswordHasher | None = None) -> None:
        self._hasher = hasher or argon2.PasswordHasher()
        self._unknown_user_hash: str | None = None

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, password_hash, password)
        except VerificationError, InvalidHashError:
            return False

    async def verify_unknown(self, password: str) -> None:
        if self._unknown_user_hash is None:
            # Same parameters as real hashes, so the work matches a real verification.
            self._unknown_user_hash = await self.hash(secrets.token_urlsafe(32))
        await self.verify(self._unknown_user_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        """True when the hash was made with weaker parameters than the current ones."""
        return self._hasher.check_needs_rehash(password_hash)
