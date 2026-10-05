# 0019. Quote pricing: a snapshot per line, re-priced while draft, frozen once submitted

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

Prices come only from the pricing engine (brief §4, rule 1;
[ADR-0004](0004-pricing-waterfall-breakdown-and-approval-metric.md)). A quote must still explain
each price after the rules, the catalog or the tenant's tax rate change, a manager must approve the
numbers the customer will see, and an order (M6) copies the accepted prices without recomputing
them. So a quote needs to know what it stores and when it prices again.

How established systems do it:

- **Salesforce CPQ** keeps every price of the waterfall on the quote line (list, special, regular,
  customer, partner, net) and recalculates them when the quote is calculated
  ([Trailhead](https://trailhead.salesforce.com/content/learn/modules/price-rules-in-salesforce-cpq/learn-when-to-use-special-price-field)).
- **Dynamics 365 Sales** recalculates a draft quote, and an
  [activated quote is read-only](https://learn.microsoft.com/en-gb/training/modules/process-sales-orders-dynamics-365-sales/2-quotes-and-quote-management).
- **SAP** keeps the conditions of each item (its pricing analysis) and decides when copying a
  document whether to price again: carry out new pricing, copy the manual conditions and redetermine
  the rest, or copy the prices unchanged
  ([copy control](https://community.sap.com/t5/enterprise-resource-planning-q-a/vtaa-pricing-copy-control-issue/qaq-p/8662334)).

## Decision

- **A snapshot per line:** the engine's result for the line (list unit price, every step with its
  stage, rule, label, rate, amount and unit price after it, the net unit price, list, net and cost
  totals, the margin floor that applied), the product as it was priced (SKU, name, unit), the
  quantity, and the manual override with the person who set it. The quote keeps its totals, the tax
  rate and approval threshold used, and when it was priced. Responses read the snapshot; nothing is
  recomputed on read.
- **While a draft,** every change to its lines (add, change, remove) prices all of them again with
  what is effective then: the rules, the products' prices and costs, the tenant's tax rate and
  threshold. Changing the terms (`valid_until`, notes) does not price again.
- **Submitting** prices once more and freezes the quote: approval, the customer and the order all
  see the stored numbers. A change afterwards is a revision (ADR-0005).
- **Revising** copies each line's product, quantity and override and prices the rest again (SAP's
  "copy the manual conditions, redetermine the others"); orders will copy the snapshot unchanged.
- An archived product or customer cannot be priced (ADR-0016): a draft holding one must drop that
  line before any other change. A quote has at most 100 lines, like a price preview.

## Alternatives considered

- **Store the inputs and price on read:** a sent quote would change when a rule does, and the
  approval would no longer match what the customer holds.
- **Price each line once, when added:** one quote would mix lines priced on different dates, with a
  tax rate or threshold that may be stale.
- **Freeze at sending instead of submitting:** the manager would approve numbers that could still
  change before the customer sees them.

## Consequences

- **Positive:** a stored quote explains itself and never drifts; the same edit gives the same
  numbers; orders inherit exactly what was accepted.
- **Negative:** every line change loads all of the quote's products and the tenant's effective
  rules; a draft's prices may change between two edits (responses say when it was priced); the
  snapshot duplicates product names and prices on purpose.
