import uuid

import pytest

from pricewright.domain.users import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    InvalidUserError,
    Role,
    User,
    WeakPasswordError,
    normalize_email,
    normalize_password,
)


def test_normalize_email_trims_and_lowercases() -> None:
    assert normalize_email("  Avery.Admin@Northfield.Example ") == "avery.admin@northfield.example"


@pytest.mark.parametrize(
    "email", ["", "avery", "avery@", "@northfield.example", "avery@northfield", "a b@x.example"]
)
def test_normalize_email_rejects_malformed_address(email: str) -> None:
    with pytest.raises(InvalidUserError, match="email"):
        normalize_email(email)


def test_normalize_email_rejects_address_longer_than_rfc_5321_allows() -> None:
    with pytest.raises(InvalidUserError, match="email"):
        normalize_email("a" * 250 + "@x.example")


@pytest.mark.parametrize("length", [MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH])
def test_normalize_password_accepts_lengths_at_the_limits(length: int) -> None:
    assert normalize_password("p" * length) == "p" * length


@pytest.mark.parametrize("length", [0, MIN_PASSWORD_LENGTH - 1, MAX_PASSWORD_LENGTH + 1])
def test_normalize_password_rejects_lengths_outside_the_policy(length: int) -> None:
    with pytest.raises(WeakPasswordError, match="password"):
        normalize_password("p" * length)


def test_normalize_password_applies_nfkc_so_equivalent_input_matches() -> None:
    composed = "contraseña segura 2026"  # "ñ" as one code point
    decomposed = "contraseña segura 2026"  # "n" + combining tilde

    assert normalize_password(decomposed) == normalize_password(composed)


def test_normalize_password_accepts_spaces_and_any_characters() -> None:
    passphrase = "correct horse battery staple ✓"

    assert normalize_password(passphrase) == passphrase


def test_user_create_normalizes_email_and_name_and_assigns_uuid7() -> None:
    tenant_id = uuid.uuid7()

    user = User.create(
        tenant_id=tenant_id,
        email="Avery@Northfield.Example",
        full_name="  Avery Admin ",
        role=Role.ADMIN,
        password_hash="hash",
    )

    assert (user.tenant_id, user.email, user.full_name) == (
        tenant_id,
        "avery@northfield.example",
        "Avery Admin",
    )
    assert user.is_active
    assert user.id.version == 7


@pytest.mark.parametrize("full_name", ["", "  ", "x" * 201])
def test_user_create_rejects_blank_or_too_long_name(full_name: str) -> None:
    with pytest.raises(InvalidUserError, match="full_name"):
        User.create(
            tenant_id=uuid.uuid7(),
            email="avery@northfield.example",
            full_name=full_name,
            role=Role.SALES_REP,
            password_hash="hash",
        )
