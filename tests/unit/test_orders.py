"""Orders converted from accepted quotes, and their cancellation (ADR-0023)."""

import copy
import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest

from pricewright.domain.errors import RuleViolationError
from pricewright.domain.orders import (
    MAX_CUSTOMER_REFERENCE_LENGTH,
    CustomerSnapshot,
    InvalidOrderError,
    OrderStatus,
)
from pricewright.domain.pricing import ArchivedCustomerError, RateOverride
from pricewright.domain.quote_lifecycle import InvalidTransitionError, QuoteStatus
from pricewright.domain.quotes import LineChange, Quote
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
)

LATER = NOW + timedelta(days=3)


def accepted() -> Quote:
    """Sent and accepted, with a manager's override on one line."""
    quote = draft(
        LineChange(BOLTS.id, Decimal(3)),
        LineChange(GLOVES.id, Decimal(40), RateOverride(Decimal("0.05"), "Loyalty")),
        by=MANAGER,
    )
    quote.submit(by=MANAGER, context=context())
    quote.send(now=NOW)
    quote.accept(now=NOW)
    return quote


def test_convert_copies_the_accepted_snapshot_unchanged() -> None:
    quote = accepted()

    order = quote.convert(number="ORD-2026-000001", customer=ACME, by=REP, now=LATER)

    assert (order.tenant_id, order.number, order.currency) == (TENANT, "ORD-2026-000001", "USD")
    assert (order.quote_id, order.quote_number) == (quote.id, "NF-2026-000001")
    assert [
        (line.product_id, line.sku, line.product_name, line.unit, line.quantity, line.pricing)
        for line in order.lines
    ] == [
        (line.product_id, line.sku, line.product_name, line.unit, line.quantity, line.pricing)
        for line in quote.lines
    ]
    assert [(line.override, line.override_by) for line in order.lines] == [
        (None, None),
        (RateOverride(Decimal("0.05"), "Loyalty"), MANAGER.id),
    ]
    totals = quote.totals
    assert order.totals.list_subtotal == totals.list_subtotal
    assert (order.totals.net_subtotal, order.totals.tax, order.totals.total) == (
        totals.net_subtotal,
        totals.tax,
        totals.total,
    )
    assert (order.totals.tax_rate, order.totals.priced_at) == (Decimal("0.0725"), NOW)
    assert (order.created_by, order.created_at, order.status_changed_at) == (REP, LATER, LATER)
    assert (order.status, order.version, order.cancel_reason) == (OrderStatus.OPEN, 1, None)


def test_convert_moves_the_quote_to_converted_and_links_the_order() -> None:
    quote = accepted()

    order = quote.convert(number="ORD-2026-000001", customer=ACME, by=REP, now=LATER)

    assert (quote.status, quote.status_changed_at, quote.order_id) == (
        QuoteStatus.CONVERTED,
        LATER,
        order.id,
    )
    assert quote.allowed_actions(LATER) == frozenset()


def test_convert_keeps_the_customer_as_it_was() -> None:
    customer = dataclasses.replace(ACME, tax_id="DE123456789", payment_terms_days=45)
    quote = accepted()

    order = quote.convert(number="ORD-2026-000001", customer=customer, by=REP, now=LATER)
    customer.change(name="Acme Holdings", payment_terms_days=60)

    assert order.customer == CustomerSnapshot(
        id=ACME.id,
        account_number="C-1001",
        name="Acme",
        tax_id="DE123456789",
        payment_terms_days=45,
    )


def test_convert_of_a_later_revision_keeps_its_display_number() -> None:
    first = draft(LineChange(BOLTS.id, Decimal(1)))
    first.submit(by=REP, context=context())
    second = first.revise(by=REP, context=context())
    second.submit(by=REP, context=context())
    second.send(now=NOW)
    second.accept(now=NOW)

    order = second.convert(number="ORD-2026-000001", customer=ACME, by=REP, now=NOW)

    assert order.quote_number == "NF-2026-000001-R2"


def test_convert_after_valid_until_because_the_customer_accepted_in_time() -> None:
    quote = accepted()
    long_after = NOW + timedelta(days=90)
    assert long_after.date() > IN_A_MONTH

    order = quote.convert(number="ORD-2026-000001", customer=ACME, by=REP, now=long_after)

    assert order.created_at == long_after


@pytest.mark.parametrize(
    "status",
    [status for status in QuoteStatus if status is not QuoteStatus.ACCEPTED],
)
def test_convert_refuses_every_quote_that_is_not_accepted(status: QuoteStatus) -> None:
    quote = accepted()
    quote.status = status
    before = copy.deepcopy(quote)

    with pytest.raises(InvalidTransitionError, match="cannot convert"):
        quote.convert(number="ORD-2026-000001", customer=ACME, by=REP, now=LATER)

    assert quote == before


def test_convert_happens_once() -> None:
    quote = accepted()
    quote.convert(number="ORD-2026-000001", customer=ACME, by=REP, now=LATER)

    with pytest.raises(InvalidTransitionError, match="converted"):
        quote.convert(number="ORD-2026-000002", customer=ACME, by=REP, now=LATER)


def test_convert_needs_the_quotes_own_active_customer() -> None:
    quote = accepted()
    archived = copy.deepcopy(ACME)
    archived.change(is_active=False)
    someone_else = dataclasses.replace(ACME, id=BOLTS.id)
    before = copy.deepcopy(quote)

    with pytest.raises(ArchivedCustomerError, match="archived"):
        quote.convert(number="ORD-2026-000001", customer=archived, by=REP, now=LATER)
    with pytest.raises(ValueError, match="not this quote's"):
        quote.convert(number="ORD-2026-000001", customer=someone_else, by=REP, now=LATER)

    assert quote == before


@pytest.mark.parametrize(
    ("reference", "kept"),
    [(None, None), ("", None), ("   ", None), (" PO-4500123 ", "PO-4500123"), ("P" * 35, "P" * 35)],
)
def test_convert_keeps_the_customers_reference_trimmed(
    reference: str | None, kept: str | None
) -> None:
    order = accepted().convert(
        number="ORD-2026-000001", customer=ACME, by=REP, now=LATER, reference=reference
    )

    assert order.customer_reference == kept


def test_convert_refuses_a_customer_reference_that_is_too_long() -> None:
    quote = accepted()
    before = copy.deepcopy(quote)

    with pytest.raises(InvalidOrderError, match=str(MAX_CUSTOMER_REFERENCE_LENGTH)):
        quote.convert(
            number="ORD-2026-000001", customer=ACME, by=REP, now=LATER, reference="P" * 36
        )

    assert quote == before
    assert issubclass(InvalidOrderError, RuleViolationError)  # a 422


def test_cancel_withdraws_an_open_order_with_its_reason() -> None:
    order = accepted().convert(number="ORD-2026-000001", customer=ACME, by=REP, now=LATER)
    much_later = LATER + timedelta(days=1)

    order.cancel(reason="  Entered for the wrong customer ", now=much_later)

    assert (order.status, order.status_changed_at, order.cancel_reason) == (
        OrderStatus.CANCELLED,
        much_later,
        "Entered for the wrong customer",
    )


def test_cancel_happens_once_and_needs_a_reason() -> None:
    order = accepted().convert(number="ORD-2026-000001", customer=ACME, by=REP, now=LATER)

    for reason in ("", "  ", "r" * 201):
        with pytest.raises(InvalidOrderError, match="reason"):
            order.cancel(reason=reason, now=LATER)
    order.cancel(reason="Duplicate", now=LATER)
    with pytest.raises(InvalidTransitionError, match="cancelled"):
        order.cancel(reason="Again", now=LATER)

    assert order.cancel_reason == "Duplicate"
