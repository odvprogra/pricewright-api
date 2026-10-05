"""``pricewright-admin``: operator commands, an adapter like the HTTP API.

Tenants are onboarded by an operator, not through the API (brief §2 has no platform role):

    pricewright-admin create-tenant --name "Northfield Supply" --currency USD --tax-rate 0.0725 \\
        --quote-prefix NF --admin-email avery@northfield.example --admin-name "Avery Admin"

The admin password is prompted for, or read from standard input with ``--password-stdin`` (scripts,
seeds). It is never an argument, so it cannot end up in the shell history or the process list.
"""

import argparse
import getpass
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TextIO

from pricewright.application.onboarding import RegisterTenant, register_tenant
from pricewright.application.ports import PasswordHasher, UnitOfWorkFactory
from pricewright.domain.errors import DomainError
from pricewright.domain.tenants import (
    DEFAULT_APPROVAL_THRESHOLD,
    DEFAULT_QUOTE_PREFIX,
    DEFAULT_QUOTE_VALIDITY_DAYS,
)

EXIT_OK = 0
EXIT_FAILED = 1


@dataclass(frozen=True, slots=True)
class Console:
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    ask_password: Callable[[str], str] = getpass.getpass


class PasswordMismatchError(Exception):
    pass


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a decimal number: {text!r}") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pricewright-admin", description="Operator commands.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-tenant", help="register a distributor and its first admin")
    create.add_argument("--name", required=True)
    create.add_argument("--currency", required=True, help="ISO 4217 code, e.g. USD")
    create.add_argument("--tax-rate", required=True, type=_decimal, help="0.0725 for 7.25%%")
    create.add_argument("--approval-threshold", type=_decimal, default=DEFAULT_APPROVAL_THRESHOLD)
    create.add_argument(
        "--quote-prefix",
        default=DEFAULT_QUOTE_PREFIX,
        help="starts every quote number, e.g. NF for NF-2026-000123",
    )
    create.add_argument(
        "--quote-validity-days",
        type=int,
        default=DEFAULT_QUOTE_VALIDITY_DAYS,
        help="how long a new quote is valid",
    )
    create.add_argument("--admin-email", required=True)
    create.add_argument("--admin-name", required=True)
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the admin password from standard input",
    )
    return parser


def _admin_password(args: argparse.Namespace, console: Console) -> str:
    if args.password_stdin:
        return console.stdin.readline().removesuffix("\n")
    password = console.ask_password("Admin password: ")
    if console.ask_password("Repeat the password: ") != password:
        raise PasswordMismatchError("the passwords do not match")
    return password


async def run(
    argv: Sequence[str],
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
    console: Console,
) -> int:
    """Run one command and return the process exit code."""
    args = _parser().parse_args(argv)
    try:
        command = RegisterTenant(
            name=args.name,
            currency=args.currency,
            tax_rate=args.tax_rate,
            approval_threshold=args.approval_threshold,
            quote_prefix=args.quote_prefix,
            quote_validity_days=args.quote_validity_days,
            admin_email=args.admin_email,
            admin_full_name=args.admin_name,
            admin_password=_admin_password(args, console),
        )
        registered = await register_tenant(command, unit_of_work=unit_of_work, hasher=hasher)
    except (DomainError, PasswordMismatchError) as error:
        console.stderr.write(f"error: {error}\n")
        return EXIT_FAILED
    console.stdout.write(
        f"Registered tenant {registered.tenant_id} with admin {registered.admin_id}\n"
    )
    return EXIT_OK
