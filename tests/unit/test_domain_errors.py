import pytest

from pricewright.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    RuleViolationError,
)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (NotFoundError, "not_found"),
        (ConflictError, "conflict"),
        (RuleViolationError, "rule_violation"),
    ],
)
def test_domain_errors_expose_stable_codes(error: type[DomainError], code: str) -> None:
    assert error.code == code
    assert issubclass(error, DomainError)
