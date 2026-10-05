# 0005. Quote lifecycle: an explicit transition table, revisions that supersede, expiry by the clock

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

A quote moves from draft to approval, to the customer and to an order (brief §4, rule 4). Revising a
sent or rejected quote supersedes it (decision D-07); every transition checks `valid_until`, and a
job persists `EXPIRED` later (D-08). The brief left open whether to use the State pattern or an
explicit table.

How established systems model the same lifecycle:

- **Dynamics 365 Sales:** a draft is editable;
  [an activated quote is read-only](https://learn.microsoft.com/en-gb/training/modules/process-sales-orders-dynamics-365-sales/2-quotes-and-quote-management),
  and any change is a revision: the original closes as _Revised_ and a new draft keeps the number
  with the next revision. Active quotes
  [close as Lost, Canceled or Revised](https://learn.microsoft.com/en-us/dynamics365/sales/close-quote);
  an accepted quote cannot be revised. Dataverse stores the
  [allowed status transitions as a table](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/define-custom-state-model-transitions).
- **SAP:** [status profiles](https://blogs.sap.com/2015/02/04/configuring-user-status-management/)
  declare which business transactions each status allows; a quotation past its validity is
  [completed](https://answers.sap.com/questions/8271276/status-of-quotation-to-be-closed.html).
- **Salesforce:** one
  [primary quote](https://trailhead.salesforce.com/content/learn/modules/salesforce-cpq-order-generation/generate-your-first-order)
  per opportunity becomes the order; submitters may
  [recall](https://developer.salesforce.com/docs/atlas.en-us.api_meta.meta/api_meta/meta_approvalprocess.htm)
  a pending approval.
- **Stripe:** [draft → open → accepted or canceled](https://docs.stripe.com/quotes/overview); a
  quote the customer rejects is canceled, an expired one cannot be accepted, and revisions keep the
  number (`QT-…-0001-2`). **Odoo** treats validity as information only.
- The State pattern suits many states whose behavior changes often, and
  ["can be overkill if a state machine has only a few states or rarely changes"](https://refactoring.guru/design-patterns/state).

## Decision

- **A transition table** (`domain/quote_lifecycle.py`): for each status, the actions it allows and
  the statuses each leads to. The quote aggregate looks actions up in it; guards that need data
  (approval, who decides) live in the aggregate. An action the status does not allow is
  `invalid_transition`, a 409.
- **Statuses:** `draft`, `pending_approval`, `approved`, `sent`, `accepted`, `converted` (M6),
  `rejected`, `cancelled`, `expired`, `superseded`. Terminal: `cancelled`, `converted`,
  `superseded`. Only drafts change.
- **Transitions** (the brief's, plus three agreed with the maintainer):
  - `submit`: draft → `approved`, or `pending_approval` when the quote needs approval (rule 3).
  - `approve` / `reject`; and `recall`: pending → draft, since nobody decided or saw anything yet.
  - `send`: approved → sent; `accept`: sent → accepted; `convert`: accepted → converted.
  - `cancel` from every open status (draft, pending, approved, sent, rejected), with a reason: it is
    also how a customer's "no" is recorded.
  - `revise` from approved, sent, rejected and expired: the revision becomes `superseded`, linked to
    a new revision in draft with the same number (D-07). Any change after approval is a revision, so
    approved content never changes.
- **Expiry by the clock.** `valid_until` is a date, valid through that day in UTC until tenants have
  time zones (ADR-0018). An offer in flight (pending, approved or sent) past its date is treated as
  `expired` whether or not the status was persisted: it allows only `revise`, plus the `expire` that
  the M5 job uses to persist it. Anything else is `quote_expired`, a 409. So the answer never
  depends on when the job ran. Drafts do not expire, but submitting one needs a `valid_until` that
  has not passed; accepted quotes convert after their date, because the customer accepted in time.

## Alternatives considered

- **State pattern:** one class per status for behavior that is mostly "allowed or not"; the table is
  shorter, and tests and documentation can walk it.
- **A state machine library** (`transitions`): a dependency for a dictionary.
- **Expiry strictly terminal** (Stripe): equally consistent, but a customer who comes back late
  would need a new quote with a new number.
- **Editing an approved quote in place** and re-approving (Salesforce's smart approvals, SAP's
  release reset): every change would have to be compared with what was approved; revisions make the
  approved content immutable instead.
- **Revise only from sent or rejected** (the brief's diagram): a change before sending would mean
  cancelling and starting a new quote.

## Consequences

- **Positive:** the lifecycle is one readable table; property tests prove that no sequence of
  actions reaches an unknown status or leaves a terminal one, that every status is reachable and
  none is a dead end, and that an expired offer behaves the same before and after the job.
- **Negative:** each revision is a new row, so a quote's history spans several records; a pending
  approval that expires can only be revised, not recalled; `valid_until` in UTC may end a few hours
  before or after local midnight.
