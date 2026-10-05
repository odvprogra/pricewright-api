"""Adding, changing and removing quote lines through the use cases, with in-memory fakes."""

import uuid
from decimal import Decimal

import pytest

from pricewright.application.quotes import (
    LineEdit,
    NewQuote,
    add_quote_line,
    change_quote_line,
    remove_quote_line,
)
from pricewright.domain.actors import Actor
from pricewright.domain.audit import AuditAction, AuditEvent
from pricewright.domain.auth import Permission, PermissionDeniedError, Principal
from pricewright.domain.catalog import UnknownProductError
from pricewright.domain.errors import NotFoundError, StaleVersionError
from pricewright.domain.pricing import PriceOverride, RateOverride
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import LineChange, Quote, QuoteNotEditableError
from pricewright.domain.users import Role
from tests.unit.test_quote_management import Fixture, usd

CLEARANCE = RateOverride(Decimal("0.1"), "Clearance")


@pytest.fixture
def f() -> Fixture:
    return Fixture()


def manager(f: Fixture) -> Principal:
    return Principal(f.northfield.id, uuid.uuid7(), Role.SALES_MANAGER)


def events(f: Fixture, action: AuditAction) -> list[AuditEvent]:
    return [event for event in f.database.audit_events.values() if event.action is action]


async def add(
    f: Fixture,
    quote: Quote,
    change: LineChange,
    *,
    caller: Principal | None = None,
    version: int = 1,
) -> Quote:
    return await add_quote_line(
        caller or f.rep,
        quote.id,
        change,
        expected_version=version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def edit(
    f: Fixture,
    quote: Quote,
    line_id: uuid.UUID,
    change: LineEdit,
    *,
    caller: Principal | None = None,
) -> Quote:
    stored = f.database.quotes[quote.id]
    return await change_quote_line(
        caller or f.rep,
        quote.id,
        line_id,
        change,
        expected_version=stored.version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def remove(f: Fixture, quote: Quote, line_id: uuid.UUID) -> Quote:
    return await remove_quote_line(
        f.rep,
        quote.id,
        line_id,
        expected_version=f.database.quotes[quote.id].version,
        unit_of_work=f.unit_of_work,
        clock=f.clock,
    )


async def test_add_quote_line_prices_it_saves_a_version_and_records_it(f: Fixture) -> None:
    quote = await f.create()

    changed = await add(f, quote, LineChange(f.bolts.id, Decimal(2)))

    assert (changed.version, len(changed.lines)) == (2, 2)
    assert f.database.quotes[quote.id] == changed
    line = changed.lines[-1]
    [event] = events(f, AuditAction.QUOTE_LINE_ADDED)
    assert event.changes == {
        "line_id": (None, str(line.id)),
        "sku": (None, "FAS-M6-100"),
        "quantity": (None, "2.000"),
        "net_total": (None, "200.0000"),
        "lines": (1, 2),
        "net_subtotal": ("900.0000", "1100.0000"),
        "tax": ("65.2500", "79.7500"),
        "total": ("965.2500", "1179.7500"),
    }


async def test_an_integration_with_quotes_manage_adds_lines(f: Fixture) -> None:
    integration = f.integration(Permission.QUOTES_MANAGE)
    quote = await f.create(caller=integration)

    changed = await add(f, quote, LineChange(f.bolts.id, Decimal(1)), caller=integration)

    assert changed.lines[-1].added_by == Actor.of(integration)


async def test_a_manager_overrides_a_line_and_is_remembered(f: Fixture) -> None:
    quote = await f.create()
    boss = manager(f)

    changed = await add(f, quote, LineChange(f.bolts.id, Decimal(1), CLEARANCE), caller=boss)

    line = changed.lines[-1]
    assert (line.override, line.override_by) == (CLEARANCE, boss.subject_id)
    [event] = events(f, AuditAction.QUOTE_LINE_ADDED)
    assert event.changes["override"] == (None, "rate 0.1000: Clearance")


@pytest.mark.parametrize("scopes", [(), (Permission.QUOTES_MANAGE,)], ids=["rep", "integration"])
async def test_overrides_need_quotes_override(f: Fixture, scopes: tuple[Permission, ...]) -> None:
    quote = await f.create()
    caller = f.integration(*scopes) if scopes else f.rep

    with pytest.raises(PermissionDeniedError, match="quotes:override"):
        await add(f, quote, LineChange(f.bolts.id, Decimal(1), CLEARANCE), caller=caller)
    with pytest.raises(PermissionDeniedError, match="quotes:override"):
        await f.create(
            NewQuote(f.acme.id, lines=[LineChange(f.bolts.id, Decimal(1), CLEARANCE)]), caller
        )

    assert f.database.quotes[quote.id].version == 1


async def test_add_quote_line_based_on_an_old_version_is_rejected(f: Fixture) -> None:
    quote = await f.create()
    await add(f, quote, LineChange(f.bolts.id, Decimal(1)))

    with pytest.raises(StaleVersionError):
        await add(f, quote, LineChange(f.bolts.id, Decimal(2)), version=1)

    assert len(f.database.quotes[quote.id].lines) == 2


async def test_add_quote_line_refuses_unknown_products_and_submitted_quotes(f: Fixture) -> None:
    quote = await f.create()

    with pytest.raises(UnknownProductError):
        await add(f, quote, LineChange(f.their_product.id, Decimal(1)))
    f.database.quotes[quote.id].status = QuoteStatus.APPROVED
    with pytest.raises(QuoteNotEditableError):
        await add(f, quote, LineChange(f.bolts.id, Decimal(1)))


async def test_another_tenants_quote_gets_no_lines(f: Fixture) -> None:
    quote = await f.create()
    stranger = Principal(f.larkspur.id, uuid.uuid7(), Role.ADMIN)

    with pytest.raises(NotFoundError, match="no such quote"):
        await add(f, quote, LineChange(f.their_product.id, Decimal(1)), caller=stranger)


async def test_change_quote_line_records_what_changed_on_that_line(f: Fixture) -> None:
    quote = await f.create()
    line = quote.lines[0]

    changed = await edit(f, quote, line.id, LineEdit(quantity=Decimal(12)))

    assert changed.lines[0].quantity == Decimal(12)
    [event] = events(f, AuditAction.QUOTE_LINE_CHANGED)
    assert event.changes == {
        "line_id": (str(line.id), str(line.id)),
        "quantity": ("10.000", "12.000"),
        "net_total": ("900.0000", "1080.0000"),
        "net_subtotal": ("900.0000", "1080.0000"),
        "tax": ("65.2500", "78.3000"),
        "total": ("965.2500", "1158.3000"),
    }


async def test_only_managers_set_or_clear_an_override_on_a_line(f: Fixture) -> None:
    quote = await f.create()
    line_id = quote.lines[0].id
    boss = manager(f)
    agreed = PriceOverride(usd("80"), "Agreed price")

    with pytest.raises(PermissionDeniedError, match="quotes:override"):
        await edit(f, quote, line_id, LineEdit(override=agreed))
    with_override = await edit(f, quote, line_id, LineEdit(override=agreed), caller=boss)
    with pytest.raises(PermissionDeniedError, match="quotes:override"):
        await edit(f, quote, line_id, LineEdit(override=None))
    cleared = await edit(f, quote, line_id, LineEdit(override=None), caller=boss)

    assert with_override.lines[0].pricing.net_total == usd("800.00")
    assert (cleared.lines[0].override, cleared.lines[0].override_by) == (None, None)


async def test_remove_quote_line_records_the_line_it_removed(f: Fixture) -> None:
    quote = await f.create()
    line = quote.lines[0]

    changed = await remove(f, quote, line.id)

    assert changed.lines == []
    [event] = events(f, AuditAction.QUOTE_LINE_REMOVED)
    assert event.changes == {
        "line_id": (str(line.id), None),
        "sku": ("FAS-M6-100", None),
        "quantity": ("10.000", None),
        "net_total": ("900.0000", None),
        "lines": (1, 0),
        "net_subtotal": ("900.0000", "0.0000"),
        "tax": ("65.2500", "0.0000"),
        "total": ("965.2500", "0.0000"),
    }


async def test_a_line_that_is_not_on_the_quote_is_not_found(f: Fixture) -> None:
    quote = await f.create()

    with pytest.raises(NotFoundError) as error:
        await remove(f, quote, uuid.uuid7())

    assert error.value.code == "not_found"
