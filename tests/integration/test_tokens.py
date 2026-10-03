"""The JWT adapter with the real library: what it accepts and everything it must reject."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from pricewright.application.ports import AccessTokens
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.users import Role
from pricewright.infrastructure.tokens import AUDIENCE, ISSUER, JwtAccessTokens

SECRET = secrets.token_urlsafe(32)  # generated, so no secret-looking literal is committed
TTL = timedelta(minutes=15)
PRINCIPAL = Principal(tenant_id=uuid.uuid7(), subject_id=uuid.uuid7(), role=Role.ADMIN)


def forged(claims: dict[str, object], *, secret: str = SECRET, typ: str = "at+jwt") -> str:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(PRINCIPAL.subject_id),
        "tid": str(PRINCIPAL.tenant_id),
        "role": "admin",
        "iat": now,
        "exp": now + TTL,
        "jti": "1",
    }
    return jwt.encode(base | claims, secret, algorithm="HS256", headers={"typ": typ})


def test_access_tokens_round_trip_the_principal() -> None:
    tokens: AccessTokens = JwtAccessTokens(SECRET, TTL)  # also checks it satisfies the port

    issued = tokens.issue(PRINCIPAL)

    assert tokens.read(issued.token) == PRINCIPAL
    assert issued.expires_in == 900
    assert jwt.get_unverified_header(issued.token)["typ"] == "at+jwt"


def test_access_tokens_reject_an_expired_token() -> None:
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    token = JwtAccessTokens(SECRET, TTL, clock=lambda: long_ago).issue(PRINCIPAL).token

    with pytest.raises(AuthenticationError):
        JwtAccessTokens(SECRET, TTL).read(token)


@pytest.mark.parametrize(
    "token",
    [
        forged({}, secret=secrets.token_urlsafe(32)),
        forged({}, typ="JWT"),  # an ID token or any other JWT is not an access token
        forged({"aud": "someone-else"}),
        forged({"iss": "someone-else"}),
        forged({"role": "owner"}),
        forged({"tid": "not-a-uuid"}),
        "not.a.jwt",
    ],
    ids=["signature", "type", "audience", "issuer", "role", "tenant", "garbage"],
)
def test_access_tokens_reject_tokens_they_must_not_trust(token: str) -> None:
    with pytest.raises(AuthenticationError):
        JwtAccessTokens(SECRET, TTL).read(token)


def test_access_tokens_reject_unsigned_tokens() -> None:
    unsigned = jwt.encode(
        {"sub": str(PRINCIPAL.subject_id)}, key=None, algorithm="none", headers={"typ": "at+jwt"}
    )

    with pytest.raises(AuthenticationError):
        JwtAccessTokens(SECRET, TTL).read(unsigned)


def test_access_tokens_reject_a_token_missing_a_required_claim() -> None:
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(PRINCIPAL.subject_id),
            "exp": datetime.now(UTC) + TTL,
        },
        SECRET,
        algorithm="HS256",
        headers={"typ": "at+jwt"},
    )

    with pytest.raises(AuthenticationError):
        JwtAccessTokens(SECRET, TTL).read(token)


def test_access_tokens_are_never_issued_to_service_accounts() -> None:
    service_account = Principal(tenant_id=uuid.uuid7(), subject_id=uuid.uuid7())

    with pytest.raises(ValueError, match="API keys"):
        JwtAccessTokens(SECRET, TTL).issue(service_account)
