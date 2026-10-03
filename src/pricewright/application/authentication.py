"""Signing in with an email and a password (ADR-0007)."""

from dataclasses import dataclass, field

from pricewright.application.ports import (
    AccessTokens,
    IdentityLookup,
    IssuedToken,
    PasswordHasher,
    UnitOfWorkFactory,
)
from pricewright.domain.auth import AuthenticationError, Principal
from pricewright.domain.errors import RuleViolationError
from pricewright.domain.users import User, canonical_password, normalize_email

# One message for every failure: callers cannot tell an unknown email from a wrong password or a
# locked account (OWASP Authentication Cheat Sheet).
INVALID_CREDENTIALS = "invalid email or password"


@dataclass(frozen=True, slots=True)
class Credentials:
    email: str
    password: str = field(repr=False)


async def log_in(
    credentials: Credentials,
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
    access_tokens: AccessTokens,
) -> IssuedToken:
    """Verify the password and issue an access token; count failures toward the lockout."""
    password = canonical_password(credentials.password)
    async with unit_of_work() as uow:
        user = await _find(uow.identities, credentials.email)
        if user is None:
            await hasher.verify_unknown(password)
            raise AuthenticationError(INVALID_CREDENTIALS)

        uow.bind_tenant(user.tenant_id)
        password_matches = await hasher.verify(user.password_hash, password)
        if not user.can_sign_in:
            raise AuthenticationError(INVALID_CREDENTIALS)
        if not password_matches:
            user.record_failed_login()
            await uow.users.save(user)
            await uow.commit()
            raise AuthenticationError(INVALID_CREDENTIALS)

        user.record_successful_login()
        if hasher.needs_rehash(user.password_hash):
            user.password_hash = await hasher.hash(password)
        await uow.users.save(user)
        await uow.commit()

    return access_tokens.issue(Principal(tenant_id=user.tenant_id, user_id=user.id, role=user.role))


async def _find(identities: IdentityLookup, raw_email: str) -> User | None:
    try:
        email = normalize_email(raw_email)
    except RuleViolationError:
        return None  # a malformed email cannot belong to anyone
    return await identities.user_by_email(email)


async def current_user(principal: Principal, *, unit_of_work: UnitOfWorkFactory) -> User:
    """The signed-in user, as stored now: a deactivated or deleted user is no longer signed in."""
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        user = await uow.users.get(principal.user_id)
    if user is None or not user.can_sign_in:
        raise AuthenticationError("the account behind this token can no longer sign in")
    return user
