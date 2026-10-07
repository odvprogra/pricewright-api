"""``pricewright-admin``: operator commands, an adapter like the HTTP API.

Tenants are onboarded by an operator, not through the API (brief §2 has no platform role):

    pricewright-admin create-tenant --name "Northfield Supply" --currency USD --tax-rate 0.0725 \\
        --quote-prefix NF --order-prefix NFO --admin-email avery@northfield.example \\
        --admin-name "Avery Admin"

The admin password is prompted for, or read from standard input with ``--password-stdin`` (scripts,
seeds). It is never an argument, so it cannot end up in the shell history or the process list.

``pricewright-admin seed`` loads the demo tenants, only in local and test environments
(ADR-0024); ``just seed`` resets the local database first.
"""

import argparse
import getpass
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date
from decimal import Decimal, InvalidOperation
from typing import TextIO

from pricewright.application.onboarding import RegisterTenant, register_tenant
from pricewright.application.ports import Clock, PasswordHasher, UnitOfWorkFactory
from pricewright.demo.seed import DEFAULT_SEED, DEMO_PASSWORD, SeedReport, seed_demo
from pricewright.domain.errors import DomainError
from pricewright.domain.tenants import (
    DEFAULT_APPROVAL_THRESHOLD,
    DEFAULT_ORDER_PREFIX,
    DEFAULT_QUOTE_PREFIX,
    DEFAULT_QUOTE_VALIDITY_DAYS,
)
from pricewright.domain.users import EmailAlreadyRegisteredError

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


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def _add_create_tenant(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    create.add_argument(
        "--order-prefix",
        default=DEFAULT_ORDER_PREFIX,
        help="starts every order number, e.g. NFO for NFO-2026-000045; not the quote prefix",
    )
    create.add_argument("--admin-email", required=True)
    create.add_argument("--admin-name", required=True)
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the admin password from standard input",
    )


def _add_seed(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    seed = commands.add_parser(
        "seed", help="load the demo tenants into a local or test database (ADR-0024)"
    )
    seed.add_argument(
        "--as-of", type=_date, help="the demo's today, YYYY-MM-DD (default: today in UTC)"
    )
    seed.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="the same seed and date give the same data"
    )
    seed.add_argument(
        "--check",
        action="store_true",
        help="only check that demo data may be loaded here; `just seed` runs it before resetting",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pricewright-admin", description="Operator commands.")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_create_tenant(commands)
    _add_seed(commands)
    return parser


def _admin_password(args: argparse.Namespace, console: Console) -> str:
    if args.password_stdin:
        return console.stdin.readline().removesuffix("\n")
    password = console.ask_password("Admin password: ")
    if console.ask_password("Repeat the password: ") != password:
        raise PasswordMismatchError("the passwords do not match")
    return password


async def _create_tenant(
    args: argparse.Namespace,
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
    console: Console,
) -> int:
    try:
        command = RegisterTenant(
            name=args.name,
            currency=args.currency,
            tax_rate=args.tax_rate,
            approval_threshold=args.approval_threshold,
            quote_prefix=args.quote_prefix,
            quote_validity_days=args.quote_validity_days,
            order_prefix=args.order_prefix,
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


def _seed_summary(report: SeedReport) -> str:
    lines = [f"Loaded the demo data as of {report.as_of.isoformat()} (seed {report.seed}):"]
    for tenant in report.tenants:
        counts = ", ".join(f"{count} {kind}" for kind, count in tenant.counts.items())
        prefixes = f"{tenant.quote_prefix} quotes, {tenant.order_prefix} orders"
        lines.append(f"  {tenant.name} ({prefixes}): {counts}")
    lines.append(f'Sign in as any of them with the passphrase "{DEMO_PASSWORD}":')
    lines.extend(
        f"  {person.email:<26} {person.role.value:<14} {tenant.name}"
        for tenant in report.tenants
        for person in tenant.people
    )
    return "\n".join(lines) + "\n"


async def _seed(
    args: argparse.Namespace,
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
    console: Console,
    clock: Clock,
) -> int:
    as_of = args.as_of or clock().astimezone(UTC).date()
    try:
        report = await seed_demo(
            unit_of_work=unit_of_work, hasher=hasher, as_of=as_of, seed=args.seed
        )
    except EmailAlreadyRegisteredError:
        console.stderr.write(
            "error: the demo data is already loaded; `just seed` resets the local database "
            "and loads it again\n"
        )
        return EXIT_FAILED
    console.stdout.write(_seed_summary(report))
    return EXIT_OK


async def run(
    argv: Sequence[str],
    *,
    unit_of_work: UnitOfWorkFactory,
    hasher: PasswordHasher,
    console: Console,
    clock: Clock,
    demo_data_allowed: bool,
) -> int:
    """Run one command and return the process exit code.

    ``demo_data_allowed`` is false in deployed environments: demo users share a published
    passphrase (ADR-0024).
    """
    args = _parser().parse_args(argv)
    if args.command == "create-tenant":
        return await _create_tenant(args, unit_of_work=unit_of_work, hasher=hasher, console=console)
    if not demo_data_allowed:
        console.stderr.write("error: demo data is loaded only where ENVIRONMENT is local or test\n")
        return EXIT_FAILED
    if args.check:  # like Rails' db:check_protected_environments before a reset
        console.stdout.write("Demo data may be loaded here.\n")
        return EXIT_OK
    return await _seed(args, unit_of_work=unit_of_work, hasher=hasher, console=console, clock=clock)
