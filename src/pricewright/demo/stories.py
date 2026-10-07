"""The demo tenants' history: each quote told as a story of dated steps (ADR-0024).

A story is one quote number, from its draft to where it stands on the as-of date: an order, a
customer's "no", a rejection, an expired offer, or a quote still in flight. Stories are data: what
happens, who does it and when. ``history.py`` plays them through the use cases, and fails loudly
if the pricing engine disagrees with a story (a quote meant to need approval that does not).

Dates are business hours in UTC, every step after the one before, and the last before the as-of
date. Quotes meant to need approval go to gold customers in quantities that pass the tenant's
threshold (``StoryPlan.heavy``); the others may need it or not, and are approved when they do.
"""

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from functools import partial

from pricewright.demo.randomness import between, chance, pick
from pricewright.demo.tenants import DemoTenant, ProductSpec, StoryKind
from pricewright.domain.catalog import UnitOfMeasure
from pricewright.domain.customers import CustomerTier
from pricewright.domain.quote_lifecycle import QuoteStatus


class Act(StrEnum):
    CREATE = "create"
    ADD_LINE = "add line"
    CHANGE_QUANTITY = "change quantity"
    OVERRIDE = "override"
    SUBMIT = "submit"
    RECALL = "recall"
    APPROVE = "approve"
    REJECT = "reject"
    SEND = "send"
    ACCEPT = "accept"
    CONVERT = "convert"
    CANCEL = "cancel"
    REVISE = "revise"
    CANCEL_ORDER = "cancel order"


@dataclass(frozen=True, slots=True)
class Step:
    at: datetime
    act: Act
    by: str
    """The email of the person who acts."""
    line: int = 0
    """Which line of the current revision a line step is about."""
    sku: str | None = None
    quantity: int | None = None
    price: Decimal | None = None
    """An override's net unit price."""
    text: str | None = None
    """A comment, a reason or the customer's reference."""
    expect: QuoteStatus | None = None
    """The status the step must leave the quote in."""
    optional: bool = False
    """An approval that is skipped when the quote did not need one."""


@dataclass(frozen=True, slots=True)
class Story:
    kind: StoryKind
    customer: str
    """The account number."""
    lines: tuple[tuple[str, int], ...]
    """SKU and quantity of each line at creation."""
    steps: tuple[Step, ...]
    """The first one creates the quote."""


class StoryError(ValueError):
    """A story the tenant cannot tell, or one that would end after the as-of date."""


OPENING = time(14, tzinfo=UTC)
"""9 a.m. in the US Central time zone."""
WORKDAY_MINUTES = 300
LINE_COUNTS = (1, 2, 3, 3, 4, 4, 5, 5, 6)
LIGHT_QUANTITY: Mapping[UnitOfMeasure, tuple[int, int]] = {
    UnitOfMeasure.BOX: (2, 30),
    UnitOfMeasure.CASE: (1, 12),
    UnitOfMeasure.EACH: (1, 24),
    UnitOfMeasure.PAIR: (6, 36),
    UnitOfMeasure.PACKAGE: (2, 20),
}
OTHER_QUANTITY = (1, 8)
REFERENCE_SHARE = 0.7
"""Most customers send a purchase order number with their acceptance."""

REJECTIONS = (
    "The discount is too deep for this volume; bring the quantities down to the next bracket.",
    "Margins on these lines are below what we can hold; rework the quantities and resubmit.",
    "We cannot hold this price on the full quantity; offer it on a smaller order.",
)
APPROVALS = (None, None, "Approved for this account.", "Fine for this volume.")
LOSSES = (
    "The customer chose another supplier",
    "Project postponed to next quarter",
    "Budget not approved",
    "The customer stopped responding",
)
WITHDRAWALS = ("Duplicate of another quote", "Request withdrawn by the customer")
ORDER_CANCELLATIONS = (
    "The customer cancelled the project",
    "Converted against the wrong purchase order",
    "Delivery date could not be met",
)
OVERRIDES = (
    "Match a competitor's price on this project",
    "Price agreed for a one-off tender",
)
HOURS = (0, 0)


@dataclass(frozen=True, slots=True)
class Cast:
    """Who acts in a tenant's stories, by email."""

    owners: Mapping[str, str]
    """Each account's rep, who builds, sends and converts its quotes."""
    approver: str
    """Decides the reps' approvals."""
    senior: str
    """Decides the approvals of quotes the approver helped build."""
    manager: str | None
    """Builds quotes and sets overrides, when the tenant has one."""


@dataclass(slots=True)
class _Script:
    """A story being written: each step is dated after the one before."""

    director: Director
    kind: StoryKind
    customer: str
    lines: tuple[tuple[str, int], ...]
    steps: list[Step] = field(default_factory=list)

    def then(
        self,
        act: Act,
        by: str,
        days: tuple[int, int] = HOURS,
        *,
        line: int = 0,
        sku: str | None = None,
        quantity: int | None = None,
        price: Decimal | None = None,
        text: str | None = None,
        expect: QuoteStatus | None = None,
        optional: bool = False,
    ) -> _Script:
        at = self.director.after(self.steps[-1].at, days)
        step = Step(at, act, by, line, sku, quantity, price, text, expect, optional)
        self.steps.append(step)
        return self

    def story(self) -> Story:
        if self.steps[-1].at >= datetime.combine(self.director.as_of, time(tzinfo=UTC)):
            raise StoryError(f"a {self.kind} story would end after {self.director.as_of}")
        return Story(self.kind, self.customer, self.lines, tuple(self.steps))


class Director:
    """Writes a tenant's stories from its plan, with one generator for all of them."""

    def __init__(self, tenant: DemoTenant, rng: random.Random, as_of: date) -> None:
        self.tenant, self.rng, self.as_of = tenant, rng, as_of
        manager = tenant.manager
        self.cast = Cast(
            owners=tenant.account_owners,
            approver=tenant.approver.email,
            senior=tenant.admin.email,
            manager=None if manager is None else manager.email,
        )
        self.products = {product.sku: product for product in tenant.products}
        self.gold = [c.account_number for c in tenant.customers if c.tier is CustomerTier.GOLD]
        self.heavy = [p for p in tenant.products if p.category in tenant.stories.heavy]

    # --- When ---------------------------------------------------------------------------------

    def _workday(self, day: date) -> datetime:
        start = datetime.combine(day, OPENING)
        return start + timedelta(minutes=between(self.rng, 0, WORKDAY_MINUTES))

    def opening(self, days_before: tuple[int, int]) -> datetime:
        return self._workday(self.as_of - timedelta(days=between(self.rng, *days_before)))

    def after(self, at: datetime, days: tuple[int, int]) -> datetime:
        """Within the hour, or on a later day's business hours."""
        later = between(self.rng, *days)
        if later == 0:
            return at + timedelta(minutes=between(self.rng, 10, 60))
        return self._workday(at.date() + timedelta(days=later))

    # --- What and for whom --------------------------------------------------------------------

    def quantity(self, product: ProductSpec) -> int:
        return between(self.rng, *LIGHT_QUANTITY.get(product.unit, OTHER_QUANTITY))

    def light(self) -> tuple[str, tuple[tuple[str, int], ...]]:
        """Any account and a few products in everyday quantities."""
        customer = pick(self.rng, list(self.cast.owners))
        skus = self._distinct(list(self.products), pick(self.rng, LINE_COUNTS))
        return customer, tuple((sku, self.quantity(self.products[sku])) for sku in skus)

    def heavy_lines(self) -> tuple[str, tuple[tuple[str, int], ...]]:
        """A gold account buying enough of the heavy categories to need approval."""
        if not self.gold or not self.heavy:
            raise StoryError(f"{self.tenant.name} has no gold customer or heavy category")
        customer = pick(self.rng, self.gold)
        skus = self._distinct([p.sku for p in self.heavy], between(self.rng, 2, 5))
        ranges = self.tenant.stories.heavy
        lines = tuple(
            (sku, between(self.rng, *ranges[self.products[sku].category])) for sku in skus
        )
        return customer, lines

    def eased(self, sku: str) -> int:
        return between(self.rng, *self.tenant.stories.eased[self.products[sku].category])

    def _distinct(self, skus: Sequence[str], count: int) -> list[str]:
        chosen: list[str] = []
        while len(chosen) < min(count, len(skus)):
            sku = pick(self.rng, skus)
            if sku not in chosen:
                chosen.append(sku)
        return chosen

    def reference(self) -> str | None:
        return (
            f"PO-{between(self.rng, 10000, 99999)}" if chance(self.rng, REFERENCE_SHARE) else None
        )

    def override_price(self, sku: str) -> Decimal:
        """Just above cost: below any margin floor, so the quote needs approval."""
        cost = self.products[sku].unit_cost
        return (cost * Decimal("1.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def script(
        self, kind: StoryKind, days_before: tuple[int, int], *, heavy: bool, by: str | None = None
    ) -> _Script:
        customer, lines = self.heavy_lines() if heavy else self.light()
        author = by or self.cast.owners[customer]
        create = Step(self.opening(days_before), Act.CREATE, author)
        return _Script(self, kind, customer, lines, [create])


# --- The stories ------------------------------------------------------------------------------
# Each starts within a range of days before the as-of date chosen so that every step happens
# before it, and offers are accepted or cancelled while still valid (30 days at Northfield, 21 at
# Larkspur; acceptance comes at most 20 days after the quote is created).

_A = Act
_PENDING = QuoteStatus.PENDING_APPROVAL


def _approve(s: _Script, d: Director, *, required: bool, by: str | None = None) -> _Script:
    comment = pick(d.rng, APPROVALS)
    who = by or d.cast.approver
    return s.then(_A.APPROVE, who, (0, 1), text=comment, optional=not required)


def _offer(s: _Script, d: Director, *, needs_approval: bool) -> _Script:
    """Submit, get approval when needed, and send."""
    rep = s.steps[0].by
    expect = _PENDING if needs_approval else None
    s.then(_A.SUBMIT, rep, expect=expect)
    _approve(s, d, required=needs_approval)
    return s.then(_A.SEND, rep, (0, 1))


def _close(s: _Script, d: Director, accept_within: tuple[int, int] = (2, 15)) -> _Script:
    """The customer accepts and the rep converts the quote into an order."""
    rep = s.steps[0].by
    s.then(_A.ACCEPT, rep, accept_within)
    return s.then(_A.CONVERT, rep, (0, 3), text=d.reference())


def _won(d: Director, kind: StoryKind = StoryKind.WON) -> Story:
    heavy = chance(d.rng, 0.25) and bool(d.gold) and bool(d.heavy)
    s = d.script(kind, (45, 178), heavy=heavy)
    _close(_offer(s, d, needs_approval=heavy), d, (2, 18))
    if kind is StoryKind.WON_ORDER_CANCELLED:
        s.then(_A.CANCEL_ORDER, s.steps[0].by, (1, 8), text=pick(d.rng, ORDER_CANCELLATIONS))
    return s.story()


def _won_after_rejection(d: Director) -> Story:
    s = d.script(StoryKind.WON_AFTER_REJECTION, (75, 178), heavy=True)
    rep = s.steps[0].by
    s.then(_A.SUBMIT, rep, expect=_PENDING)
    s.then(_A.REJECT, d.cast.approver, (0, 1), text=pick(d.rng, REJECTIONS))
    s.then(_A.REVISE, rep, (1, 3))
    for index, (sku, _) in enumerate(s.lines):
        s.then(_A.CHANGE_QUANTITY, rep, line=index, quantity=d.eased(sku))
    return _close(_offer(s, d, needs_approval=False), d).story()


def _won_after_change(d: Director) -> Story:
    s = _offer(
        d.script(StoryKind.WON_AFTER_CHANGE, (75, 178), heavy=False), d, needs_approval=False
    )
    rep = s.steps[0].by
    s.then(_A.REVISE, rep, (2, 8))
    _, quantity = s.lines[0]
    s.then(_A.CHANGE_QUANTITY, rep, line=0, quantity=quantity + between(d.rng, 1, 10))
    return _close(_offer(s, d, needs_approval=False), d, (2, 12)).story()


def _after_expiry(d: Director, kind: StoryKind) -> Story:
    won = kind is StoryKind.WON_AFTER_EXPIRY
    s = d.script(kind, (100, 178) if won else (46, 60), heavy=False)
    _offer(s, d, needs_approval=False)
    # The customer comes back after the offer expired: a revision, priced again.
    s.then(_A.REVISE, s.steps[0].by, (33, 45) if won else (34, 40))
    _offer(s, d, needs_approval=False)
    return (_close(s, d, (2, 10)) if won else s).story()


def _won_after_recall(d: Director) -> Story:
    s = d.script(StoryKind.WON_AFTER_RECALL, (50, 178), heavy=True)
    rep = s.steps[0].by
    s.then(_A.SUBMIT, rep, expect=_PENDING)
    s.then(_A.RECALL, rep)
    s.then(_A.CHANGE_QUANTITY, rep, line=0, quantity=d.eased(s.lines[0][0]))
    return _close(_offer(s, d, needs_approval=False), d, (2, 14)).story()


def _won_with_override(d: Director) -> Story:
    manager = _manager(d)
    s = d.script(StoryKind.WON_WITH_OVERRIDE, (45, 178), heavy=False)
    rep = s.steps[0].by
    price = d.override_price(s.lines[0][0])
    s.then(_A.OVERRIDE, manager, (0, 1), line=0, price=price, text=pick(d.rng, OVERRIDES))
    s.then(_A.SUBMIT, rep, expect=_PENDING)
    _approve(s, d, required=True, by=d.cast.senior)  # the manager helped build it
    s.then(_A.SEND, rep, (0, 1))
    return _close(s, d).story()


def _manager_offer(d: Director) -> Story:
    manager = _manager(d)
    s = d.script(StoryKind.MANAGER_OFFER, (4, 15), heavy=True, by=manager)
    s.then(_A.SUBMIT, manager, expect=_PENDING)
    _approve(s, d, required=True, by=d.cast.senior)
    return s.then(_A.SEND, manager, (0, 1)).story()


def _manager(d: Director) -> str:
    if d.cast.manager is None:
        raise StoryError(f"{d.tenant.name} has no sales manager for this story")
    return d.cast.manager


def _lost(d: Director) -> Story:
    s = _offer(d.script(StoryKind.LOST, (35, 178), heavy=False), d, needs_approval=False)
    return s.then(_A.CANCEL, s.steps[0].by, (5, 18), text=pick(d.rng, LOSSES)).story()


def _cancelled(d: Director, kind: StoryKind) -> Story:
    s = d.script(kind, (10, 170), heavy=False)
    rep = s.steps[0].by
    if kind is StoryKind.CANCELLED_APPROVED:
        s.then(_A.SUBMIT, rep)
        _approve(s, d, required=False)
    return s.then(_A.CANCEL, rep, (1, 5), text=pick(d.rng, WITHDRAWALS)).story()


def _rejected(d: Director) -> Story:
    s = d.script(StoryKind.REJECTED, (3, 60), heavy=True)
    s.then(_A.SUBMIT, s.steps[0].by, expect=_PENDING)
    return s.then(_A.REJECT, d.cast.approver, (0, 1), text=pick(d.rng, REJECTIONS)).story()


def _in_flight(d: Director, kind: StoryKind) -> Story:
    """Quotes that stop where the kind says, still valid on the as-of date or expired by it."""
    days: Mapping[StoryKind, tuple[int, int]] = {
        StoryKind.EXPIRED_SENT: (33, 120),
        StoryKind.EXPIRED_APPROVED: (33, 120),
        StoryKind.EXPIRED_PENDING: (33, 90),
        StoryKind.PENDING: (1, 6),
        StoryKind.APPROVED: (2, 7),
        StoryKind.SENT: (3, 15),
        StoryKind.ACCEPTED: (9, 15),
    }
    heavy = kind in {StoryKind.EXPIRED_PENDING, StoryKind.PENDING}
    s = d.script(kind, days[kind], heavy=heavy)
    rep = s.steps[0].by
    s.then(_A.SUBMIT, rep, expect=_PENDING if heavy else None)
    if not heavy:
        _approve(s, d, required=False)
    if kind in {StoryKind.EXPIRED_SENT, StoryKind.SENT, StoryKind.ACCEPTED}:
        s.then(_A.SEND, rep, (0, 1))
    if kind is StoryKind.ACCEPTED:
        s.then(_A.ACCEPT, rep, (2, 6))
    return s.story()


def _draft(d: Director) -> Story:
    s = d.script(StoryKind.DRAFT, (1, 10), heavy=False)
    rep = s.steps[0].by
    others = [sku for sku in d.products if sku not in dict(s.lines)]
    if chance(d.rng, 0.5) and others:
        sku = pick(d.rng, others)
        s.then(_A.ADD_LINE, rep, sku=sku, quantity=d.quantity(d.products[sku]))
    if chance(d.rng, 0.3):
        s.then(_A.CHANGE_QUANTITY, rep, line=0, quantity=s.lines[0][1] + between(d.rng, 1, 5))
    return s.story()


_K = StoryKind
TELLERS: Mapping[StoryKind, Callable[[Director], Story]] = {
    _K.WON: _won,
    _K.WON_ORDER_CANCELLED: partial(_won, kind=_K.WON_ORDER_CANCELLED),
    _K.WON_AFTER_REJECTION: _won_after_rejection,
    _K.WON_AFTER_CHANGE: _won_after_change,
    _K.WON_AFTER_EXPIRY: partial(_after_expiry, kind=_K.WON_AFTER_EXPIRY),
    _K.SENT_AFTER_EXPIRY: partial(_after_expiry, kind=_K.SENT_AFTER_EXPIRY),
    _K.WON_AFTER_RECALL: _won_after_recall,
    _K.WON_WITH_OVERRIDE: _won_with_override,
    _K.MANAGER_OFFER: _manager_offer,
    _K.LOST: _lost,
    _K.CANCELLED_DRAFT: partial(_cancelled, kind=_K.CANCELLED_DRAFT),
    _K.CANCELLED_APPROVED: partial(_cancelled, kind=_K.CANCELLED_APPROVED),
    _K.REJECTED: _rejected,
    _K.EXPIRED_SENT: partial(_in_flight, kind=_K.EXPIRED_SENT),
    _K.EXPIRED_APPROVED: partial(_in_flight, kind=_K.EXPIRED_APPROVED),
    _K.EXPIRED_PENDING: partial(_in_flight, kind=_K.EXPIRED_PENDING),
    _K.PENDING: partial(_in_flight, kind=_K.PENDING),
    _K.APPROVED: partial(_in_flight, kind=_K.APPROVED),
    _K.SENT: partial(_in_flight, kind=_K.SENT),
    _K.ACCEPTED: partial(_in_flight, kind=_K.ACCEPTED),
    _K.DRAFT: _draft,
}
"""Every kind of story, and how to tell one."""


def tell_stories(tenant: DemoTenant, rng: random.Random, as_of: date) -> tuple[Story, ...]:
    """Every story of the tenant's plan, in the plan's order (the history plays them by date)."""
    director = Director(tenant, rng, as_of)
    return tuple(
        TELLERS[kind](director)
        for kind, count in tenant.stories.counts.items()
        for _ in range(count)
    )
