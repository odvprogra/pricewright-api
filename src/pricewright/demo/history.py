"""Play the demo tenants' stories through the use cases, in the order things happened (ADR-0024).

Steps of every story are interleaved by date and played with the clock set to each one, so quote
and order numbers follow creation dates and the audit trail reads in time order. A story the
application disagrees with (a quote meant to need approval that does not) stops the load: the
demo data never silently drifts from its script.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from pricewright.application.orders import cancel_order
from pricewright.application.ports import UnitOfWorkFactory
from pricewright.application.quotes import (
    LineEdit,
    NewQuote,
    accept_quote,
    add_quote_line,
    approve_quote,
    cancel_quote,
    change_quote_line,
    convert_quote,
    create_quote,
    recall_quote,
    reject_quote,
    revise_quote,
    send_quote,
    submit_quote,
)
from pricewright.demo.clock import DemoClock
from pricewright.demo.stories import Act, Step, Story
from pricewright.domain.auth import Principal
from pricewright.domain.money import Money
from pricewright.domain.orders import Order
from pricewright.domain.pricing import PriceOverride
from pricewright.domain.quote_lifecycle import QuoteStatus
from pricewright.domain.quotes import LineChange, Quote


class HistoryError(RuntimeError):
    """The application did something a story did not expect."""


@dataclass(slots=True)
class _Thread:
    """A story being played: its current revision and its order, once there is one."""

    story: Story
    quote: Quote | None = None
    order: Order | None = None

    @property
    def current(self) -> Quote:
        if self.quote is None:
            raise HistoryError(f"a {self.story.kind} story acts before its quote exists")
        return self.quote

    @property
    def placed(self) -> Order:
        if self.order is None:
            raise HistoryError(f"a {self.story.kind} story acts on an order it has not placed")
        return self.order


@dataclass(frozen=True, slots=True)
class Roster:
    """The ids the stories' names stand for, in one tenant."""

    people: Mapping[str, Principal]
    products: Mapping[str, UUID]
    customers: Mapping[str, UUID]
    currency: str


class HistoryPlayer:
    def __init__(self, roster: Roster, unit_of_work: UnitOfWorkFactory, clock: DemoClock) -> None:
        self._roster, self._unit_of_work, self._clock = roster, unit_of_work, clock
        self._acts: Mapping[Act, Callable[[_Thread, Step, Principal], Awaitable[None]]] = {
            Act.CREATE: self._create,
            Act.ADD_LINE: self._add_line,
            Act.CHANGE_QUANTITY: self._change_quantity,
            Act.OVERRIDE: self._override,
            Act.SUBMIT: self._submit,
            Act.RECALL: self._recall,
            Act.APPROVE: self._approve,
            Act.REJECT: self._reject,
            Act.SEND: self._send,
            Act.ACCEPT: self._accept,
            Act.CONVERT: self._convert,
            Act.CANCEL: self._cancel,
            Act.REVISE: self._revise,
            Act.CANCEL_ORDER: self._cancel_order,
        }

    async def play(self, stories: Sequence[Story]) -> None:
        threads = [_Thread(story) for story in stories]
        events = sorted(
            (step.at, story_index, step_index)
            for story_index, story in enumerate(stories)
            for step_index, step in enumerate(story.steps)
        )
        for at, story_index, step_index in events:
            thread = threads[story_index]
            step = thread.story.steps[step_index]
            self._clock.set(at)
            await self._acts[step.act](thread, step, self._roster.people[step.by])

    # --- Drafts -------------------------------------------------------------------------------

    def _line(self, sku: str | None, quantity: int | None) -> LineChange:
        if sku is None or quantity is None:
            raise HistoryError("a line step needs a SKU and a quantity")
        return LineChange(self._roster.products[sku], Decimal(quantity))

    async def _create(self, thread: _Thread, _step: Step, by: Principal) -> None:
        story = thread.story
        new = NewQuote(
            customer_id=self._roster.customers[story.customer],
            lines=[self._line(sku, quantity) for sku, quantity in story.lines],
        )
        created = await create_quote(by, new, unit_of_work=self._unit_of_work, clock=self._clock)
        thread.quote = created.value

    async def _add_line(self, thread: _Thread, step: Step, by: Principal) -> None:
        quote = thread.current
        thread.quote = await add_quote_line(
            by,
            quote.id,
            self._line(step.sku, step.quantity),
            expected_version=quote.version,
            unit_of_work=self._unit_of_work,
            clock=self._clock,
        )

    async def _edit(self, thread: _Thread, step: Step, by: Principal, edit: LineEdit) -> None:
        quote = thread.current
        thread.quote = await change_quote_line(
            by,
            quote.id,
            quote.lines[step.line].id,
            edit,
            expected_version=quote.version,
            unit_of_work=self._unit_of_work,
            clock=self._clock,
        )

    async def _change_quantity(self, thread: _Thread, step: Step, by: Principal) -> None:
        quantity = None if step.quantity is None else Decimal(step.quantity)
        await self._edit(thread, step, by, LineEdit(quantity=quantity))

    async def _override(self, thread: _Thread, step: Step, by: Principal) -> None:
        if step.price is None or step.text is None:
            raise HistoryError("an override needs a price and a reason")
        override = PriceOverride(Money(step.price, self._roster.currency), step.text)
        await self._edit(thread, step, by, LineEdit(override=override))

    # --- Moves --------------------------------------------------------------------------------

    async def _move(
        self, thread: _Thread, by: Principal, move: Callable[..., Awaitable[Quote]], *extra: object
    ) -> None:
        quote = thread.current
        thread.quote = await move(
            by,
            quote.id,
            *extra,
            expected_version=quote.version,
            unit_of_work=self._unit_of_work,
            clock=self._clock,
        )

    async def _submit(self, thread: _Thread, step: Step, by: Principal) -> None:
        await self._move(thread, by, submit_quote)
        status = thread.current.status
        if step.expect is not None and status is not step.expect:
            raise HistoryError(f"a {thread.story.kind} quote was {status} after submitting")

    async def _recall(self, thread: _Thread, _step: Step, by: Principal) -> None:
        await self._move(thread, by, recall_quote)

    async def _approve(self, thread: _Thread, step: Step, by: Principal) -> None:
        if thread.current.status is not QuoteStatus.PENDING_APPROVAL:
            if step.optional:
                return  # it did not need approval
            raise HistoryError(f"a {thread.story.kind} quote had nothing to approve")
        await self._move(thread, by, approve_quote, step.text)

    async def _reject(self, thread: _Thread, step: Step, by: Principal) -> None:
        await self._move(thread, by, reject_quote, step.text or "")

    async def _send(self, thread: _Thread, _step: Step, by: Principal) -> None:
        await self._move(thread, by, send_quote)

    async def _accept(self, thread: _Thread, _step: Step, by: Principal) -> None:
        await self._move(thread, by, accept_quote)

    async def _cancel(self, thread: _Thread, step: Step, by: Principal) -> None:
        await self._move(thread, by, cancel_quote, step.text or "")

    async def _revise(self, thread: _Thread, _step: Step, by: Principal) -> None:
        quote = thread.current
        successor = await revise_quote(
            by,
            quote.id,
            expected_version=quote.version,
            unit_of_work=self._unit_of_work,
            clock=self._clock,
        )
        thread.quote = successor.value

    # --- Orders -------------------------------------------------------------------------------

    async def _convert(self, thread: _Thread, step: Step, by: Principal) -> None:
        quote = thread.current
        order = await convert_quote(
            by,
            quote.id,
            step.text,
            expected_version=quote.version,
            unit_of_work=self._unit_of_work,
            clock=self._clock,
        )
        thread.order, thread.quote = order.value, None  # the quote is converted: done

    async def _cancel_order(self, thread: _Thread, step: Step, by: Principal) -> None:
        order = thread.placed
        thread.order = await cancel_order(
            by,
            order.id,
            step.text or "",
            expected_version=order.version,
            unit_of_work=self._unit_of_work,
            clock=self._clock,
        )
