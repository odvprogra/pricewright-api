"""Stored form of bearer secrets (refresh tokens, API keys)."""

import hashlib


def digest(secret: str) -> str:
    """SHA-256: secrets with 200+ random bits need no slow, salted hash (unlike passwords)."""
    return hashlib.sha256(secret.encode()).hexdigest()
