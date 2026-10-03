"""Access tokens as JWTs (ADR-0007): HS256, typed ``at+jwt`` (RFC 9068), checked per RFC 8725.

Verification accepts one algorithm, requires every claim it relies on, and checks the issuer, the
audience and the explicit type, so no other kind of JWT can pass as an access token.
"""

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import jwt

from pricewright.application.ports import IssuedToken
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.users import Role
from pricewright.infrastructure.clock import utc_now

ALGORITHM = "HS256"
TYP_HEADER = "at+jwt"
ISSUER = "pricewright-api"
AUDIENCE = "pricewright-api"
_REQUIRED_CLAIMS = ["iss", "aud", "sub", "tid", "role", "iat", "exp", "jti"]


class JwtAccessTokens:
    def __init__(
        self, secret: str, ttl: timedelta, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._secret = secret
        self._ttl = ttl
        self._clock = clock

    def issue(self, principal: Principal) -> IssuedToken:
        if principal.role is None:
            raise ValueError("access tokens are for users; service accounts use API keys")
        issued_at = self._clock()
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(principal.subject_id),
            "tid": str(principal.tenant_id),
            "role": principal.role.value,
            "iat": issued_at,
            "exp": issued_at + self._ttl,
            "jti": str(uuid.uuid7()),
        }
        token = jwt.encode(claims, self._secret, algorithm=ALGORITHM, headers={"typ": TYP_HEADER})
        return IssuedToken(token=token, expires_in=int(self._ttl.total_seconds()))

    def read(self, token: str) -> Principal:
        try:
            if jwt.get_unverified_header(token).get("typ") != TYP_HEADER:
                raise AuthenticationError("not an access token")
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": _REQUIRED_CLAIMS},
            )
            return Principal(
                tenant_id=uuid.UUID(claims["tid"]),
                subject_id=uuid.UUID(claims["sub"]),
                role=Role(claims["role"]),
            )
        except (jwt.PyJWTError, ValueError) as error:
            raise AuthenticationError("invalid access token") from error
