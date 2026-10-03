"""Signing in, staying signed in, signing out, and API keys (ADR-0007)."""

from dataclasses import dataclass, field

from pricewright.application.ports import (
    AccessTokens,
    Clock,
    IdentityLookup,
    IssuedToken,
    PasswordHasher,
    UnitOfWorkFactory,
)
from pricewright.domain.auth import AuthenticationError, PermissionDeniedError, Principal
from pricewright.domain.digests import digest
from pricewright.domain.errors import RuleViolationError
from pricewright.domain.service_accounts import is_well_formed
from pricewright.domain.sessions import RefreshToken, new_refresh_token
from pricewright.domain.users import User, canonical_password, normalize_email

# One message for every failure: callers cannot tell an unknown email from a wrong password or a
# locked account (OWASP Authentication Cheat Sheet).
INVALID_CREDENTIALS = "invalid email or password"
INVALID_REFRESH = "invalid refresh token"
INVALID_API_KEY = "invalid API key"


@dataclass(frozen=True, slots=True)
class Credentials:
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class TokenPair:
    access: IssuedToken
    refresh_token: str = field(repr=False)


async def log_in(
    credentials: Credentials,
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
    access_tokens: AccessTokens,
    clock: Clock,
) -> TokenPair:
    """Verify the password and start a session; count failures toward the lockout."""
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
            await uow.users.save_login_state(user)
            await uow.commit()
            raise AuthenticationError(INVALID_CREDENTIALS)

        user.record_successful_login()
        if hasher.needs_rehash(user.password_hash):
            user.password_hash = await hasher.hash(password)
        await uow.users.save_login_state(user)
        refresh_token = new_refresh_token()
        await uow.refresh_tokens.add(
            RefreshToken.start_family(
                tenant_id=user.tenant_id, user_id=user.id, token=refresh_token, now=clock()
            )
        )
        await uow.commit()

    return TokenPair(access=access_tokens.issue(_principal(user)), refresh_token=refresh_token)


async def refresh_session(
    refresh_token: str,
    *,
    unit_of_work: UnitOfWorkFactory,
    access_tokens: AccessTokens,
    clock: Clock,
) -> TokenPair:
    """Trade a refresh token for a new pair. A token presented twice revokes its whole family."""
    now = clock()
    async with unit_of_work() as uow:
        stored = await uow.identities.refresh_token_by_digest(digest(refresh_token))
        if stored is None:
            raise AuthenticationError(INVALID_REFRESH)
        uow.bind_tenant(stored.tenant_id)

        reused = stored.was_used or not await uow.refresh_tokens.claim(stored.id, now)
        user = await uow.users.get(stored.user_id)
        if reused or user is None or not user.can_sign_in:
            # A reused token leaked: neither its holder nor the attacker keeps the session.
            await uow.refresh_tokens.revoke_family(stored.family_id, now)
            await uow.commit()
            raise AuthenticationError(INVALID_REFRESH)
        if not stored.is_usable(now):
            raise AuthenticationError(INVALID_REFRESH)

        successor_token = new_refresh_token()
        await uow.refresh_tokens.add(stored.successor(token=successor_token, now=now))
        await uow.commit()

    return TokenPair(access=access_tokens.issue(_principal(user)), refresh_token=successor_token)


async def log_out(refresh_token: str, *, unit_of_work: UnitOfWorkFactory, clock: Clock) -> None:
    """End the session the token belongs to. Unknown tokens are ignored: there is nothing to end."""
    async with unit_of_work() as uow:
        stored = await uow.identities.refresh_token_by_digest(digest(refresh_token))
        if stored is None:
            return
        uow.bind_tenant(stored.tenant_id)
        await uow.refresh_tokens.revoke_family(stored.family_id, clock())
        await uow.commit()


async def authenticate_api_key(
    key: str, *, unit_of_work: UnitOfWorkFactory, clock: Clock
) -> Principal:
    """The service account behind an API key. A malformed key fails before any lookup."""
    if not is_well_formed(key):
        raise AuthenticationError(INVALID_API_KEY)
    async with unit_of_work() as uow:
        stored = await uow.identities.api_key_by_digest(digest(key))
        if stored is None:
            raise AuthenticationError(INVALID_API_KEY)
        uow.bind_tenant(stored.tenant_id)
        now = clock()
        account = await uow.service_accounts.get(stored.service_account_id)
        if account is None or not account.is_active or not stored.is_usable(now):
            raise AuthenticationError(INVALID_API_KEY)
        if stored.record_use(now):  # at most once an hour, so most requests write nothing
            await uow.api_keys.save(stored)
            await uow.commit()
    return Principal(tenant_id=account.tenant_id, subject_id=account.id, scopes=account.scopes)


async def current_user(principal: Principal, *, unit_of_work: UnitOfWorkFactory) -> User:
    """The signed-in user, as stored now: a deactivated or deleted user is no longer signed in."""
    if principal.is_service_account:
        raise PermissionDeniedError("service accounts have no user profile")
    async with unit_of_work() as uow:
        uow.bind_tenant(principal.tenant_id)
        user = await uow.users.get(principal.subject_id)
    if user is None or not user.can_sign_in:
        raise AuthenticationError("the account behind this token can no longer sign in")
    return user


def _principal(user: User) -> Principal:
    return Principal(tenant_id=user.tenant_id, subject_id=user.id, role=user.role)


async def _find(identities: IdentityLookup, raw_email: str) -> User | None:
    try:
        email = normalize_email(raw_email)
    except RuleViolationError:
        return None  # a malformed email cannot belong to anyone
    return await identities.user_by_email(email)
