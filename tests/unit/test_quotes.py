"""Draft quotes: lines priced by the engine, a snapshot of each price, totals that reconcile."""

import dataclasses
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from pricewright.domain.audit import ActorType
from pricewright.domain.auth import Principal
from pricewright.domain.catalog import UnknownProductError
from pricewright.domain.pricing import (
    ApprovalReason,
    ArchivedProductError,
    PriceOverride,
    RateOverride,
    Stage,
)
from pricewright.domain.quantities import InvalidQuantityError
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import (
    MAX_QUOTE_LINES,
    Actor,
    InvalidQuoteError,
    LineChange,
    QuoteNotEditableError,
    UnknownQuoteLineError,
    quote_number,
)
from pricewright.domain.users import Role
from tests.unit.quote_data import (
    ACME,
    BOLTS,
    GLOVES,
    IN_A_MONTH,
    MANAGER,
    NOW,
    REP,
    TENANT,
    context,
    draft,
    margin_floor,
    product,
    usd,
    volume_tier,
)


def test_quote_number_pads_the_sequence_to_six_digits() -> None:
    assert quote_number("NF", 2026, 123) == "NF-2026-000123"
    assert quote_number("LT", 2027, 1_234_567) == "LT-2027-1234567"


def test_actor_of_a_person_or_an_integration() -> None:
    person = Principal(TENANT, uuid.uuid7(), Role.SALES_REP)
    integration = Principal(TENANT, uuid.uuid7())

    assert Actor.of(person) == Actor(ActorType.USER, person.subject_id)
    assert Actor.of(integration) == Actor(ActorType.SERVICE_ACCOUNT, integration.subject_id)


def test_draft_starts_as_revision_one_in_draft_with_the_tenants_currency() -> None:
    quote = draft()

    assert (quote.status, quote.revision, quote.version) == (QuoteStatus.DRAFT, 1, 1)
    assert (quote.tenant_id, quote.customer_id, quote.currency) == (TENANT, ACME.id, "USD")
    assert (quote.created_by, quote.created_at, quote.status_changed_at) == (REP, NOW, NOW)
    assert quote.display_number == "NF-2026-000001"
    assert quote.lines == []
    assert (quote.totals.net_subtotal, quote.totals.total) == (usd("0"), usd("0"))


def test_draft_prices_its_first_lines_with_what_is_effective_now() -> None:
    quote = draft(
        LineChange(BOLTS.id, Decimal(10)), pricing=context(rules=[volume_tier(("10", "0.1"))])
    )

    [line] = quote.lines
    assert (line.sku, line.product_name, line.unit, line.quantity) == (
        "FAS-M6-100",
        "Product FAS-M6-100",
        BOLTS.unit,
        Decimal(10),
    )
    assert [step.stage for step in line.pricing.breakdown.steps] == [Stage.VOLUME_TIER]
    assert line.pricing.net_total == usd("900.00")
    assert quote.totals.priced_at == NOW


def test_draft_keeps_its_notes_trimmed_and_refuses_long_ones() -> None:
    assert draft().notes is None
    quote = draft()
    quote.change_terms(notes="  Delivery in two weeks.  ", now=NOW)
    assert quote.notes == "Delivery in two weeks."

    with pytest.raises(InvalidQuoteError, match="notes"):
        quote.change_terms(notes="x" * 2001, now=NOW)
    assert quote.notes == "Delivery in two weeks."


def test_draft_valid_until_cannot_be_in_the_past_but_may_be_today() -> None:
    assert draft(valid_until=NOW.date()).valid_until == NOW.date()

    with pytest.raises(InvalidQuoteError, match="valid_until"):
        draft(valid_until=NOW.date() - timedelta(days=1))


def test_draft_refuses_more_lines_than_a_quote_holds() -> None:
    too_many = [LineChange(GLOVES.id, Decimal(1))] * (MAX_QUOTE_LINES + 1)

    with pytest.raises(InvalidQuoteError, match="at most 100 lines"):
        draft(*too_many)


def test_add_line_prices_the_whole_quote_and_totals_reconcile() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(2)))

    line = quote.add_line(LineChange(GLOVES.id, Decimal("3.5")), by=REP, context=context())

    assert line.pricing.net_total == usd("43.75")
    assert quote.totals.list_subtotal == usd("243.75")
    assert quote.totals.net_subtotal == usd("243.75")
    assert quote.totals.tax == usd("17.67")  # 243.75 x 7.25% = 17.671875, once on the subtotal
    assert quote.totals.total == usd("261.42")


def test_add_line_reprices_earlier_lines_with_todays_rules() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(10)))
    later = context(at=NOW + timedelta(days=1), rules=[volume_tier(("10", "0.1"))])

    quote.add_line(LineChange(GLOVES.id, Decimal(1)), by=REP, context=later)

    assert quote.lines[0].pricing.net_total == usd("900.00")
    assert quote.totals.priced_at == later.at


def test_add_line_snapshots_the_product_as_it_is_when_priced() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(1)))
    renamed = dataclasses.replace(BOLTS, name="Hex bolt M6 x 100, zinc")

    quote.add_line(
        LineChange(GLOVES.id, Decimal(1)), by=REP, context=context(products=[renamed, GLOVES])
    )

    assert quote.lines[0].product_name == "Hex bolt M6 x 100, zinc"


def test_add_line_of_an_unknown_or_archived_product_changes_nothing() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(1)))
    archived = product("OLD-1", "10", "5")
    archived.change(is_active=False)

    with pytest.raises(UnknownProductError):
        quote.add_line(LineChange(uuid.uuid7(), Decimal(1)), by=REP, context=context())
    with pytest.raises(ArchivedProductError):
        quote.add_line(
            LineChange(archived.id, Decimal(1)), by=REP, context=context(products=[BOLTS, archived])
        )

    assert [line.product_id for line in quote.lines] == [BOLTS.id]


def test_add_line_with_an_invalid_quantity_changes_nothing() -> None:
    quote = draft()

    with pytest.raises(InvalidQuantityError):
        quote.add_line(LineChange(BOLTS.id, Decimal("0.0001")), by=REP, context=context())

    assert quote.lines == []


def test_add_line_beyond_the_limit_is_refused() -> None:
    quote = draft(*[LineChange(GLOVES.id, Decimal(1))] * MAX_QUOTE_LINES)

    with pytest.raises(InvalidQuoteError, match="at most 100 lines"):
        quote.add_line(LineChange(BOLTS.id, Decimal(1)), by=REP, context=context())


def test_override_is_its_own_last_step_and_remembers_who_set_it() -> None:
    quote = draft()
    override = RateOverride(Decimal("0.1"), "Matching a competitor")

    line = quote.add_line(LineChange(BOLTS.id, Decimal(1), override), by=MANAGER, context=context())

    assert line.override == override
    assert line.override_by == MANAGER.id
    assert line.pricing.breakdown.steps[-1].stage is Stage.MANUAL_OVERRIDE
    assert line.pricing.net_total == usd("90.00")


def test_change_line_sets_the_quantity_and_keeps_the_override() -> None:
    override = PriceOverride(usd("80"), "Agreed price")
    quote = draft()
    line = quote.add_line(LineChange(BOLTS.id, Decimal(1), override), by=MANAGER, context=context())

    changed = quote.change_line(line.id, quantity=Decimal(3), by=REP, context=context())

    assert (changed.quantity, changed.override, changed.override_by) == (
        Decimal(3),
        override,
        MANAGER.id,
    )
    assert changed.pricing.net_total == usd("240.00")
    assert quote.totals.net_subtotal == usd("240.00")


def test_change_line_replaces_or_removes_the_override() -> None:
    quote = draft()
    line = quote.add_line(
        LineChange(BOLTS.id, Decimal(1), RateOverride(Decimal("0.1"), "First offer")),
        by=MANAGER,
        context=context(),
    )
    other_manager = Actor(ActorType.USER, uuid.uuid7())

    replaced = quote.change_line(
        line.id,
        override=RateOverride(Decimal("0.2"), "Second offer"),
        by=other_manager,
        context=context(),
    )
    assert (replaced.override_by, replaced.pricing.net_total) == (other_manager.id, usd("80.00"))

    removed = quote.change_line(line.id, override=None, by=REP, context=context())
    assert (removed.override, removed.override_by, removed.pricing.net_total) == (
        None,
        None,
        usd("100.00"),
    )


def test_change_line_keeps_who_added_it() -> None:
    quote = draft()
    line = quote.add_line(LineChange(BOLTS.id, Decimal(1)), by=REP, context=context())

    changed = quote.change_line(line.id, quantity=Decimal(2), by=MANAGER, context=context())

    assert changed.added_by == REP


def test_remove_line_prices_the_rest_again() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(1)), LineChange(GLOVES.id, Decimal(2)))
    bolts = quote.lines[0]

    removed = quote.remove_line(bolts.id, context=context())

    assert removed.id == bolts.id
    assert [line.product_id for line in quote.lines] == [GLOVES.id]
    assert quote.totals.net_subtotal == usd("25.00")


def test_lines_of_another_quote_are_not_found() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(1)))

    with pytest.raises(UnknownQuoteLineError):
        quote.line(uuid.uuid7())
    with pytest.raises(UnknownQuoteLineError):
        quote.remove_line(uuid.uuid7(), context=context())
    with pytest.raises(UnknownQuoteLineError):
        quote.change_line(uuid.uuid7(), quantity=Decimal(2), by=REP, context=context())


def test_change_terms_sets_valid_until_without_repricing() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(1)))
    priced_at = quote.totals.priced_at

    quote.change_terms(valid_until=date(2026, 12, 31), now=NOW + timedelta(days=3))

    assert quote.valid_until == date(2026, 12, 31)
    assert quote.totals.priced_at == priced_at


def test_change_terms_refuses_a_past_date_and_changes_nothing() -> None:
    quote = draft()

    with pytest.raises(InvalidQuoteError, match="valid_until"):
        quote.change_terms(valid_until=date(2026, 10, 1), notes="Ignored", now=NOW)

    assert (quote.valid_until, quote.notes) == (IN_A_MONTH, None)


def test_reprice_applies_what_is_effective_at_the_new_time() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(10)))

    quote.reprice(context(at=NOW + timedelta(hours=1), rules=[volume_tier(("10", "0.1"))]))

    assert quote.totals.net_subtotal == usd("900.00")


def test_discount_and_approval_reasons_come_from_the_snapshot() -> None:
    floor = margin_floor("0.2")
    quote = draft(
        LineChange(BOLTS.id, Decimal(1), RateOverride(Decimal("0.3"), "Clearance")),
        pricing=context(rules=[floor]),
    )

    assert quote.discount == Decimal("0.3000")
    assert quote.approval_reasons == (
        ApprovalReason.DISCOUNT_ABOVE_THRESHOLD,
        ApprovalReason.LINE_BELOW_MARGIN_FLOOR,
    )


def test_only_drafts_change() -> None:
    quote = draft(LineChange(BOLTS.id, Decimal(1)))
    quote.status = QuoteStatus.APPROVED
    line = quote.lines[0]

    for change in (
        lambda: quote.add_line(LineChange(GLOVES.id, Decimal(1)), by=REP, context=context()),
        lambda: quote.change_line(line.id, quantity=Decimal(2), by=REP, context=context()),
        lambda: quote.remove_line(line.id, context=context()),
        lambda: quote.change_terms(notes="Late note", now=NOW),
    ):
        with pytest.raises(
            QuoteNotEditableError, match="only drafts change; this quote is approved"
        ):
            change()

    assert quote.lines == [line]


def test_pricing_for_another_customer_is_a_programming_error() -> None:
    quote = draft()
    stranger = dataclasses.replace(ACME, id=uuid.uuid7())

    with pytest.raises(ValueError, match="another customer"):
        quote.reprice(context(customer=stranger))
