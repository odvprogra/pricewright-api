# 0004. Pricing: a staged waterfall, explained per line, and a value-weighted approval metric

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

Distributors combine volume, customer tier and promotional discounts, and managers override prices
with a reason (brief §4, rules 1 to 3). A rep must be able to say why a line costs what it costs,
and a manager must approve quotes that give too much away or sell below a margin floor.

How established systems stack discounts:

- **SAP** pricing procedures compute each condition from a reference step or subtotal
  ([discussion](https://archive.sap.com/discussions/thread/749296)).
- **Oracle Advanced Pricing** applies phases and buckets in sequence; within a phase only one
  modifier per incompatibility group applies, chosen by best price or precedence
  ([pricing phases](https://docs.oracle.com/cd/E18727_01/doc.121/e13428/T327893T327911.htm)).
- **Salesforce CPQ** runs a sequential waterfall: list, special, regular (volume), customer
  (manual), partner, net
  ([Trailhead](https://trailhead.salesforce.com/content/learn/modules/discounting-tools-in-salesforce-cpq/configure-partner-and-distributor-discounts)).
- **Dynamics 365** resolves concurrent discounts by priority, as exclusive, best price or compounded
  ([Learn](https://learn.microsoft.com/en-us/dynamics365/commerce/discounts-pos)).

How they explain the result: SAP's
[pricing analysis](https://help.sap.com/saphelp_46c/helpdata/en/93/743531546011d1a7020000e829fd11/content.htm)
lists every condition of an item, found or not; Salesforce CPQ keeps each waterfall price on the
quote line
([overview](https://nebulaconsulting.co.uk/insights/salesforce-cpq-the-price-waterfall/)); Dynamics
365's
[price details](https://learn.microsoft.com/en-us/dynamicsax-2012/appuser-itpro/enable-price-details-on-orders)
show unit prices, discounts, margin estimates and the agreements applied. Margins are gross margins
on the selling price, `(price − cost) / price`.

## Decision

- **A pure function**, `price_quote(customer, lines, settings, rules, at)`, in `domain/pricing.py`.
  The stages are a fixed tuple, and one function picks a stage's rule: no Strategy or Chain of
  Responsibility classes, since kinds differ only in which rate they offer.
- **Waterfall:** list price → volume tier → customer tier → promotion → manual override. Stages
  **cascade**: each rate applies to the unit price the previous stage left (10% + 5% = 14.5%). **One
  rule per stage**, the best rate for the customer (ties per ADR-0018). Rules are those effective at
  `at`, the pricing time (SAP's pricing date).
- **Manual override**, always last and with a reason: a rate off (Salesforce's manual discount) or
  the net unit price itself, up or down (a custom price). Its API arrives with quote lines (M4).
- **Rounding (ADR-0003):** each step's unit price rounds to four places, half up, so the steps add
  up exactly to the net unit price. A line total is the net unit price times the quantity, rounded
  to the currency's minor units; the net subtotal is the sum of line totals; tax is computed once on
  it; the total is their sum.
- **Breakdown per line:** the list unit price, then one step per stage that applied (stage, rule id
  and name or the override's reason, rate, amount per unit, unit price after it), the net unit
  price, list, net and cost totals, the margin and its rate, and the margin floor that applies.
- **Margin floor guard:** a line is below its floor when its margin is less than the floor times its
  net total; a line given away with a cost always is. The guard flags; it never changes a price.
- **Approval (decision D-06):** the discount is `1 − net subtotal / list subtotal`, before tax and
  with overrides. Approval is needed when it exceeds the tenant's threshold (compared exactly, as
  `net < list × (1 − threshold)`) or any line is below its floor; the reasons are listed.

## Alternatives considered

- **Adding discounts** (10% + 5% = 15%): easier to say, but over-discounts and is not how ERPs and
  CPQs combine stages.
- **Several rules per stage** (Dynamics 365's compounded mode): two volume tiers would count the
  same quantity twice.
- **Rounding only the final price:** the steps would no longer add up to it.
- **Approval by the largest line discount:** the margin floor already catches an abusive line; the
  value-weighted discount measures what the quote gives away.
- **Margin as markup on cost:** floors and reports read margin on price, as gross margin is quoted.
- Deferred: exclusive promotions that block every other discount.

## Consequences

- **Positive:** every price explains itself, and the same inputs give the same breakdown. Property
  tests prove that steps add up, totals reconcile, the best rule applies whatever the rules' order,
  and no line below its floor escapes approval.
- **Negative:** a cascade is harder to explain than a sum (the breakdown shows each step); a price
  override can raise a price, so a quote's discount can be negative; every pricing call reads all of
  the tenant's effective rules.
