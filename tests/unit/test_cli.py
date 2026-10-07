import io
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from pricewright import cli
from pricewright.application.ports import UnitOfWork
from pricewright.domain.users import Role
from tests.fakes import FakeClock, FakePasswordHasher, FakeUnitOfWork, InMemoryDatabase

PASSWORD = "northfield admin passphrase"
CREATE_NORTHFIELD = [
    "create-tenant",
    "--name",
    "Northfield Supply",
    "--currency",
    "USD",
    "--tax-rate",
    "0.0725",
    "--admin-email",
    "avery@northfield.example",
    "--admin-name",
    "Avery Admin",
]


class Terminal:
    """Captures what a command writes and answers its password prompts in order."""

    def __init__(self, stdin: str = "", answers: tuple[str, ...] = ()) -> None:
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self._answers: Iterator[str] = iter(answers)
        self.console = cli.Console(
            stdin=io.StringIO(stdin),
            stdout=self.stdout,
            stderr=self.stderr,
            ask_password=lambda _prompt: next(self._answers),
        )


async def run(
    argv: list[str],
    terminal: Terminal,
    database: InMemoryDatabase,
    *,
    clock: FakeClock | None = None,
    demo_data_allowed: bool = True,
) -> int:
    def unit_of_work() -> UnitOfWork:
        return FakeUnitOfWork(database)

    return await cli.run(
        argv,
        unit_of_work=unit_of_work,
        hasher=FakePasswordHasher(),
        console=terminal.console,
        clock=clock or FakeClock(),
        demo_data_allowed=demo_data_allowed,
    )


async def test_create_tenant_reads_the_password_from_stdin() -> None:
    database, terminal = InMemoryDatabase(), Terminal(stdin=PASSWORD + "\n")

    exit_code = await run([*CREATE_NORTHFIELD, "--password-stdin"], terminal, database)

    [tenant] = database.tenants.values()
    [admin] = database.users.values()
    assert exit_code == cli.EXIT_OK
    assert terminal.stdout.getvalue() == f"Registered tenant {tenant.id} with admin {admin.id}\n"
    assert (tenant.name, str(tenant.settings.tax_rate), admin.role) == (
        "Northfield Supply",
        "0.0725",
        Role.ADMIN,
    )
    assert admin.password_hash == FakePasswordHasher.PREFIX + PASSWORD


async def test_create_tenant_takes_the_quote_settings_or_their_defaults() -> None:
    with_prefix, defaulted = InMemoryDatabase(), InMemoryDatabase()
    argv = [*CREATE_NORTHFIELD, "--password-stdin"]

    settings = ["--quote-prefix", "NF", "--quote-validity-days", "45", "--order-prefix", "NFO"]
    await run([*argv, *settings], Terminal(stdin=PASSWORD + "\n"), with_prefix)
    await run(argv, Terminal(stdin=PASSWORD + "\n"), defaulted)

    [given] = with_prefix.tenants.values()
    [default] = defaulted.tenants.values()
    assert (given.settings.quote_prefix, given.settings.quote_validity_days) == ("NF", 45)
    assert (default.settings.quote_prefix, default.settings.quote_validity_days) == ("QUO", 30)
    assert (given.settings.order_prefix, default.settings.order_prefix) == ("NFO", "ORD")


async def test_create_tenant_prompts_twice_for_the_password() -> None:
    database, terminal = InMemoryDatabase(), Terminal(answers=(PASSWORD, PASSWORD))

    exit_code = await run(CREATE_NORTHFIELD, terminal, database)

    assert exit_code == cli.EXIT_OK
    assert len(database.users) == 1


async def test_create_tenant_with_mismatched_passwords_fails_and_saves_nothing() -> None:
    database, terminal = InMemoryDatabase(), Terminal(answers=(PASSWORD, PASSWORD + "x"))

    exit_code = await run(CREATE_NORTHFIELD, terminal, database)

    assert exit_code == cli.EXIT_FAILED
    assert terminal.stderr.getvalue() == "error: the passwords do not match\n"
    assert database == InMemoryDatabase()


async def test_create_tenant_reports_business_errors_without_a_traceback() -> None:
    database, terminal = InMemoryDatabase(), Terminal(stdin="too short\n")

    exit_code = await run([*CREATE_NORTHFIELD, "--password-stdin"], terminal, database)

    assert exit_code == cli.EXIT_FAILED
    assert terminal.stderr.getvalue().startswith("error: password must have 15 to 128")


async def test_create_tenant_rejects_a_tax_rate_that_is_not_a_number(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [*CREATE_NORTHFIELD, "--tax-rate", "seven"]

    with pytest.raises(SystemExit) as exit_info:
        await run(argv, Terminal(), InMemoryDatabase())

    assert exit_info.value.code == 2  # argparse's usage error
    assert "not a decimal number: 'seven'" in capsys.readouterr().err


async def test_seed_loads_the_demo_tenants_and_says_who_can_sign_in() -> None:
    database, terminal = InMemoryDatabase(), Terminal()
    clock = FakeClock(datetime(2026, 10, 7, 23, 30, tzinfo=UTC))

    exit_code = await run(["seed"], terminal, database, clock=clock)

    output = terminal.stdout.getvalue()
    assert exit_code == cli.EXIT_OK
    assert output.startswith("Loaded the demo data as of 2026-10-07 (seed 2026):\n")
    assert (
        "  Northfield Supply (NF quotes, NFO orders): 5 users, 8 categories, 300 products, "
        "80 customers, 13 pricing rules\n"
    ) in output
    assert 'Sign in as any of them with the passphrase "pricewright demo":\n' in output
    assert "  morgan@northfield.example  sales_manager  Northfield Supply\n" in output
    assert "  riley@larkspur.example     sales_rep      Larkspur Tool Co.\n" in output
    assert len(database.tenants) == 2


async def test_seed_takes_the_as_of_date_and_the_seed() -> None:
    terminal = Terminal()

    exit_code = await run(
        ["seed", "--as-of", "2026-01-15", "--seed", "42"], terminal, InMemoryDatabase()
    )

    assert exit_code == cli.EXIT_OK
    assert terminal.stdout.getvalue().startswith("Loaded the demo data as of 2026-01-15 (seed 42)")


async def test_seed_refuses_a_date_that_is_not_iso(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        await run(["seed", "--as-of", "15/01/2026"], Terminal(), InMemoryDatabase())

    assert exit_info.value.code == 2
    assert "not a date (YYYY-MM-DD): '15/01/2026'" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["seed"], ["seed", "--check"]])
async def test_seed_refuses_deployed_environments_and_loads_nothing(argv: list[str]) -> None:
    database, terminal = InMemoryDatabase(), Terminal()

    exit_code = await run(argv, terminal, database, demo_data_allowed=False)

    assert exit_code == cli.EXIT_FAILED
    assert terminal.stderr.getvalue() == (
        "error: demo data is loaded only where ENVIRONMENT is local or test\n"
    )
    assert database == InMemoryDatabase()


async def test_seed_refuses_to_load_the_demo_data_twice() -> None:
    database, terminal = InMemoryDatabase(), Terminal()
    await run(["seed"], Terminal(), database)
    tenants = dict(database.tenants)

    exit_code = await run(["seed"], terminal, database)

    assert exit_code == cli.EXIT_FAILED
    assert "the demo data is already loaded; `just seed` resets" in terminal.stderr.getvalue()
    assert database.tenants == tenants


async def test_seed_check_only_says_that_demo_data_may_be_loaded() -> None:
    database, terminal = InMemoryDatabase(), Terminal()

    exit_code = await run(["seed", "--check"], terminal, database)

    assert exit_code == cli.EXIT_OK
    assert terminal.stdout.getvalue() == "Demo data may be loaded here.\n"
    assert database == InMemoryDatabase()
