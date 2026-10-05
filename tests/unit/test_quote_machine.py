"""The quote aggregate under any sequence of actions, by anyone, as time passes (hypothesis).

Invariants checked after every step:

- the status is a known one, every change of status is in the transition table (ADR-0005), and a
  terminal status is never left;
- lines and totals change only in draft, and always reconcile;
- no quote with a line below its margin floor, or above the discount threshold, is approved, sent
  or accepted without an approval decided by someone who did not build it (ADR-0020);
- at most one approval request is pending, exactly while the quote is pending approval;
- superseded revisions never change again and stay linked to their successor (decision D-07).
"""

import copy
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from hypothesis import event, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from pricewright.domain.actors import Actor
from pricewright.domain.catalog import Product
from pricewright.domain.errors import DomainError
from pricewright.domain.money import Money
from pricewright.domain.pricing import ManualOverride, PriceOverride, RateOverride
from pricewright.domain.quote_approvals import ApprovalStatus
from pricewright.domain.quote_lifecycle import TERMINAL, TRANSITIONS, QuoteStatus
from pricewright.domain.quotes import LineChange, PricingContext, Quote, QuoteLine, QuoteTotals
from tests.unit.quote_data import (
    BOLTS,
    GLOVES,
    INTEGRATION,
    MANAGER,
    NOW,
    OTHER_MANAGER,
    OTHER_REP,
    REP,
    context,
    margin_floor,
    product,
    volume_tier,
)
from tests.unit.test_quote_properties import assert_totals_reconcile

THIN = product("THIN-1", "100", "90")  # below a 20% floor even at its list price
CATALOG = (BOLTS, GLOVES, THIN)
RULES = (margin_floor("0.2"), volume_tier(("10", "0.1")))
APPROVED_OR_LATER = frozenset({QuoteStatus.APPROVED, QuoteStatus.SENT, QuoteStatus.ACCEPTED})

PEOPLE = (REP, OTHER_REP, MANAGER, OTHER_MANAGER, INTEGRATION)
people = st.sampled_from(PEOPLE)
overrides: st.SearchStrategy[ManualOverride | None] = st.sampled_from(
    [
        None,
        RateOverride(Decimal("0.3"), "Clearance"),
        RateOverride(Decimal("0.05"), "Loyalty"),
        PriceOverride(Money(Decimal("50"), "USD"), "Agreed price"),
    ]
)


def refused(action: Callable[[], object]) -> bool:
    try:
        action()
    except DomainError:
        return True
    return False


def reachable_in_one_step(before: QuoteStatus) -> set[QuoteStatus]:
    # An expired offer acts as EXPIRED (ADR-0005), so its moves count too.
    sources = [before, QuoteStatus.EXPIRED]
    return {
        status
        for source in sources
        for reached in TRANSITIONS[source].values()
        for status in reached
    }


class QuoteLifecycleMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.now: datetime = NOW
        self.quote = Quote.draft(
            number="NF-2026-000001",
            valid_until=NOW.date() + timedelta(days=30),
            by=REP,
            context=self.pricing(),
        )
        self.previous: QuoteStatus = self.quote.status
        self.terminal: QuoteStatus | None = None
        self.frozen: tuple[list[QuoteLine], QuoteTotals] | None = None
        self.retired: list[tuple[Quote, Quote]] = []
        """Superseded revisions, each with a copy taken when it was superseded."""

    def pricing(self) -> PricingContext:
        return context(at=self.now, rules=RULES, products=CATALOG)

    @initialize(
        lines=st.lists(
            st.tuples(st.sampled_from(CATALOG), st.integers(1, 20), overrides),
            min_size=1,
            max_size=3,
        ),
        by=people,
    )
    def first_lines(
        self, lines: list[tuple[Product, int, ManualOverride | None]], by: Actor
    ) -> None:
        for item, quantity, override in lines:
            change = LineChange(item.id, Decimal(quantity), override)
            self.quote.add_line(change, by=by, context=self.pricing())

    def attempt(self, action: Callable[[], object]) -> object | None:
        """Run an action; a refused one must leave the quote exactly as it was."""
        before = copy.deepcopy(self.quote)
        try:
            return action()
        except DomainError:
            assert self.quote == before
            return None

    @rule(
        item=st.sampled_from(CATALOG),
        quantity=st.integers(1, 20),
        override=overrides,
        by=people,
    )
    def add_line(
        self, item: Product, quantity: int, override: ManualOverride | None, by: Actor
    ) -> None:
        change = LineChange(item.id, Decimal(quantity), override)
        self.attempt(lambda: self.quote.add_line(change, by=by, context=self.pricing()))

    @rule(index=st.integers(0, 20))
    def remove_line(self, index: int) -> None:
        if self.quote.lines:
            line = self.quote.lines[index % len(self.quote.lines)]
            self.attempt(lambda: self.quote.remove_line(line.id, context=self.pricing()))

    @rule(days=st.integers(0, 40))
    def change_valid_until(self, days: int) -> None:
        valid_until = self.now.date() + timedelta(days=days)
        self.attempt(lambda: self.quote.change_terms(valid_until=valid_until, now=self.now))

    @rule(by=people)
    def submit(self, by: Actor) -> None:
        self.attempt(lambda: self.quote.submit(by=by, context=self.pricing()))

    @rule()
    def recall(self) -> None:
        self.attempt(lambda: self.quote.recall(now=self.now))

    @rule(by=people)
    def approve(self, by: Actor) -> None:
        self.attempt(lambda: self.quote.approve(by=by, now=self.now))

    @rule(by=people)
    def reject(self, by: Actor) -> None:
        self.attempt(lambda: self.quote.reject(by=by, comment="Too generous", now=self.now))

    @rule()
    def send(self) -> None:
        self.attempt(lambda: self.quote.send(now=self.now))

    @rule()
    def accept(self) -> None:
        self.attempt(lambda: self.quote.accept(now=self.now))

    @rule(chance=st.integers(0, 4))
    def cancel(self, chance: int) -> None:
        # Cancelling ends a run, and it is allowed from most statuses: keep it rarer.
        if chance == 0:
            self.attempt(lambda: self.quote.cancel(reason="Customer declined", now=self.now))

    @rule(by=people)
    def revise(self, by: Actor) -> None:
        old = self.quote
        successor = self.attempt(lambda: old.revise(by=by, context=self.pricing()))
        if isinstance(successor, Quote):
            self.retired.append((old, copy.deepcopy(old)))
            self.quote = successor
            self.previous, self.terminal, self.frozen = successor.status, None, None

    @rule(by=people)
    def advance(self, by: Actor) -> None:
        """The next step of the main path, so runs also reach sent and accepted quotes."""
        quote = self.quote
        match quote.status:
            case QuoteStatus.DRAFT:
                self.submit(by)
            case QuoteStatus.PENDING_APPROVAL:
                independent = [p for p in PEOPLE if p.is_person and p not in quote.builders()]
                if independent:
                    self.approve(independent[0])
            case QuoteStatus.APPROVED:
                self.send()
            case QuoteStatus.SENT:
                self.accept()
            case _:
                self.revise(by)

    @rule(days=st.integers(0, 6), hours=st.integers(0, 23))
    def time_passes(self, days: int, hours: int) -> None:
        self.now += timedelta(days=days, hours=hours)

    @rule(by=people, which=st.integers(0, 50))
    def act_on_a_superseded_revision(self, by: Actor, which: int) -> None:
        if not self.retired:
            return
        old, _ = self.retired[which % len(self.retired)]
        pricing, now = self.pricing(), self.now
        assert refused(lambda: old.revise(by=by, context=pricing))
        assert refused(lambda: old.cancel(reason="Too late", now=now))
        assert refused(lambda: old.accept(now=now))
        assert refused(lambda: old.approve(by=by, now=now))
        assert refused(
            lambda: old.add_line(LineChange(BOLTS.id, Decimal(1)), by=by, context=pricing)
        )

    @invariant()
    def status_changes_follow_the_table_and_terminal_statuses_are_final(self) -> None:
        status = self.quote.status
        assert status in set(QuoteStatus)
        event(f"reached {status.value}, revision {min(self.quote.revision, 3)}")
        if status is not self.previous:
            assert status in reachable_in_one_step(self.previous)
        if self.terminal is not None:
            assert status is self.terminal
        elif status in TERMINAL:
            self.terminal = status
        self.previous = status

    @invariant()
    def lines_change_only_in_draft_and_totals_reconcile(self) -> None:
        quote = self.quote
        assert_totals_reconcile(quote)
        if quote.status is QuoteStatus.DRAFT:
            self.frozen = None
        elif self.frozen is None:
            self.frozen = (copy.deepcopy(quote.lines), quote.totals)
        else:
            assert (quote.lines, quote.totals) == self.frozen

    @invariant()
    def nothing_needing_approval_is_approved_without_an_independent_decision(self) -> None:
        quote = self.quote
        if quote.status not in APPROVED_OR_LATER:
            return
        below_floor = any(line.pricing.below_margin_floor for line in quote.lines)
        if not (below_floor or quote.approval_reasons):
            return
        approved = [a for a in quote.approvals if a.status is ApprovalStatus.APPROVED]
        assert approved, "approved without an approval decision"
        builders = {actor.id for actor in quote.builders()}
        assert approved[-1].decided_by not in builders

    @invariant()
    def a_request_is_pending_exactly_while_the_quote_is(self) -> None:
        pending = [a for a in self.quote.approvals if a.status is ApprovalStatus.PENDING]
        assert len(pending) <= 1
        assert bool(pending) == (self.quote.status is QuoteStatus.PENDING_APPROVAL)

    @invariant()
    def superseded_revisions_never_change_and_stay_linked(self) -> None:
        chain = [old for old, _ in self.retired] + [self.quote]
        for (old, copied), successor in zip(self.retired, chain[1:], strict=True):
            assert old == copied
            assert old.status is QuoteStatus.SUPERSEDED
            assert old.superseded_by_id == successor.id
            assert successor.supersedes_id == old.id
            assert (successor.number, successor.revision) == (old.number, old.revision + 1)


QuoteLifecycleMachine.TestCase.settings = settings(max_examples=150, stateful_step_count=60)
TestQuoteLifecycleMachine = QuoteLifecycleMachine.TestCase
