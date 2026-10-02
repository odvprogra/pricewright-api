"""Business errors. The API maps them to Problem Details; other adapters map them their own way."""

from typing import ClassVar


class DomainError(Exception):
    """Base for business errors. ``code`` is a stable, machine-readable identifier."""

    code: ClassVar[str] = "domain_error"


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class RuleViolationError(DomainError):
    code = "rule_violation"
