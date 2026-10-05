# 0023. Orders: a document of their own, converted once from an accepted quote, prices copied unchanged

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

The brief ends the quote's life in an order: an accepted quote converts, idempotently, into an order
that snapshots its prices and tax (§4, rule 5; decision D-09), never recomputed (§3). `converted` is
a terminal status of the lifecycle, and an accepted quote converts after its `valid_until` because
the customer accepted in time (ADR-0005). The quote's prices were frozen at submission (ADR-0019).
Fulfilment, invoicing and payments are out of scope (§10).

How established systems turn a quote into an order:

- **SAP SD** creates a sales order with reference to the quotation, linked in the document flow.
  Copy control decides the prices: pricing type D
  ["copy pricing elements unchanged"](https://leanx.eu/en/sap/table/tvcpf), G (unchanged, taxes
  redetermined), B (new pricing). The quotation item's completion rule decides whether it is
  [completed with the first reference or only when the full quantity is referenced](https://answers.sap.com/questions/4623794/one-quotation-to-multiple-orders.html).
  Each document type has its own number range.
- **Salesforce CPQ**
  [generates the order from the primary quote](https://trailhead.salesforce.com/content/learn/modules/salesforce-cpq-order-generation/generate-your-first-order)
  when it is marked _Ordered_: account, dates and the quote lines' quantities and prices. Splitting
  a quote into
  [several orders](https://trailhead.salesforce.com/content/learn/modules/salesforce-cpq-order-generation/automatically-split-a-quote-into-multiple-orders)
  is an option.
- **Dynamics 365 Sales**
  [creates the order from a quote and closes the quote as Won](https://learn.microsoft.com/en-us/dynamics365/sales/create-edit-order-sales);
  the order's prices are locked, so catalog changes do not reach it. Orders close as fulfilled or
  canceled, and closed orders do not change.
- **Odoo** confirms the quotation itself: the same record becomes the sales order.
- **Stripe**: [accepting a quote](https://docs.stripe.com/quotes/overview) creates one invoice;
  [the invoice keeps the customer's name, tax ids and address](https://docs.stripe.com/api/invoices/object)
  once finalized, whatever later happens to the customer.

## Decision

- **A document of its own.** `Order` is a separate aggregate (`domain/orders.py`), linked both ways
  with the quote revision it came from: the order keeps the quote's id and its number as people read
  it (`NF-2026-000123-R2`), the quote keeps the order's id. Converting moves the quote to
  `converted` in the same step (`Quote.convert`).
- **Once, whole.** Only an `accepted` quote converts, at any date, and at most once: one quote, one
  order, no partial conversion (the brief's model, and the default of SAP's quotations, CPQ and
  Dynamics 365).
- **Copied unchanged.** Each line keeps the quote line's snapshot: product as priced (SKU, name,
  unit), quantity, the engine's result with every waterfall step, the manual override and who set
  it. The order keeps the quote's list and net subtotals, tax rate, tax and total, and when the
  prices were set. Nothing is priced again.
- **The customer as it was.** The order copies the customer's account number, name, tax id and
  payment terms (net days) when it is placed, as an invoice does. Quotes carry prices, not payment
  terms, so the terms come from the customer at conversion. An archived customer takes no new orders
  (ADR-0016). An optional `customer_reference` keeps the customer's own reference, such as its
  purchase order number, up to 35 characters as SAP's.
- **Statuses:** `open`, and `cancelled` with a reason: a commitment made by mistake is withdrawn,
  never deleted, as Dynamics 365 cancels orders and Stripe voids invoices. Cancelling leaves the
  quote `converted`. The status is open-ended in responses (ADR-0015): fulfilment and invoicing
  would add statuses.
- **Numbers:** a series of their own per tenant and year, with the tenant's `order_prefix`
  (`ORD-2026-000045`; ADR-0021).
- **Permissions:** `orders:read` for every role, grantable to integrations; `orders:manage` (convert
  and cancel) for every role (brief §2: reps convert), reserved for people: an order commits the
  company, like sending or accepting a quote.
- **Retries:** converting takes `If-Match` with the quote's version and an optional
  `Idempotency-Key` (ADR-0022): a retry gets the same order back, and a second order cannot exist (a
  unique quote per order).

## Alternatives considered

- **The quote becomes the order** (Odoo): one record, but a revision chain whose last revision turns
  into a different document, and an order number that is a quote number.
- **Partial conversion or several orders per quote** (SAP's completion rules, CPQ's split): needed
  for staged deliveries, which belong to fulfilment, out of scope.
- **Price again at conversion** (SAP's pricing type B): the customer would be held to prices it
  never saw.
- **Payment terms on the quote:** the offer would carry them, but quotes in M4 do not; they can be
  added to the quote's snapshot later and copied from there.
- **Immutable orders without cancellation:** simpler, but a conversion made by mistake could only be
  corrected in the database.
- **Cancelling reopens the quote:** the quote's acceptance happened; selling again takes a new
  quote.

## Consequences

- **Positive:** an order is exactly what the customer accepted, explained line by line, and keeps
  who the customer was; a property test proves that its lines and totals equal the quote's and
  reconcile, and that a quote converts only while accepted and at most once.
- **Negative:** each order duplicates its quote's lines on purpose; a customer whose details change
  after the order keeps the old ones on it; a cancelled order cannot be revived, and its quote
  cannot convert again.
