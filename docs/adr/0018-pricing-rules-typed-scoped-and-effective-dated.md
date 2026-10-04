# 0018. Pricing rules: one typed record per rule, scoped and effective-dated

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

Managers maintain the rules the pricing engine applies (brief §2): volume tiers for a product or a
category, customer tier discounts, time-boxed promotions and a margin floor guard (brief §3). Within
a stage of the waterfall the best discount wins
([ADR-0004](0004-pricing-waterfall-breakdown-and-approval-metric.md)). The brief also gave each rule
a priority.

How established systems store pricing rules:

- **SAP** keeps condition records per condition type, keyed by the fields of a condition table
  (customer and material, material group…), each with a validity period and optional quantity
  scales. An access sequence searches the tables from the most specific to the most general and can
  stop at the first record found
  ([SAP Help](https://help.sap.com/saphelp_46c/helpdata/en/2a/fdeb47b535d1118b3f0060b03ca329/content.htm),
  [exclusive indicator](https://archive.sap.com/discussions/thread/793792)).
- **Oracle Advanced Pricing** keeps typed modifier lines for an item, an item category or all items,
  with start and end dates and price breaks; conflicts are resolved by best price or by precedence
  ([pricing phases](https://docs.oracle.com/cd/E18727_01/doc.121/e13428/T327893T327911.htm)).
- **Dynamics 365** keeps every trade agreement in one table: a relation (price, line discount…), an
  account code and an item code (_Table_, _Group_ or _All_), from and to dates, and a quantity range
  ([journal lines](<https://technet.microsoft.com/library/aa553463(v=ax.60)>)).
- **Salesforce CPQ** discount schedules hold tiers by quantity; by default (_Range_) the whole line
  gets the tier it reaches, while _Slab_ discounts each portion at its own tier's rate
  ([Trailhead](https://trailhead.salesforce.com/content/learn/modules/discounting-tools-in-salesforce-cpq/override-volume-based-discounts)).

## Decision

- **One record per rule** with a kind: `volume_tier`, `customer_tier`, `promotion` or
  `margin_floor`. Its scope is one product, one category or every product; customer tier discounts
  also name the tier. Rates are fractions with four places, like the tenant's: a discount is in (0,
  1]; a margin floor, the least gross margin on the selling price, is in [0, 1). Storage: one table
  with a check per kind, plus a table of volume brackets.
- **Validity** runs from `valid_from` (inclusive) to `valid_to` (exclusive), as UTC instants;
  half-open like PostgreSQL's ranges, so consecutive windows neither overlap nor leave a gap. Rules
  are open-ended, except promotions, which must end.
- **Volume tiers** have up to ten brackets by minimum quantity. The highest bracket a line reaches
  applies to the whole line (_Range_), counting that line's quantity only.
- **No priority.** The best discount wins within a stage, so a priority would never change a price.
  Ties go to the most specific scope (a product, then a category, then every product: SAP's search
  order), then to the oldest rule, so a breakdown always names the same rule. The margin floor is a
  guard, not a discount: the most specific floor applies, so a category can allow less margin than
  the tenant-wide floor; among equally specific floors, the highest.
- **Never deleted.** Rules are deactivated or end-dated. Kind, scope and tier are the rule's
  identity and never change, like the keys of SAP's condition records: a rule for something else is
  a new rule.

## Alternatives considered

- **A table per kind** (Salesforce's separate objects): four repositories and queries for what the
  engine reads at once; a check per kind enforces the same rules.
- **Rule parameters as JSON:** no foreign keys to products and categories, no constraints.
- **Priorities** (Oracle's precedence, Dynamics 365's priorities): they matter when the first match
  wins; with best price they are only a tie-breaker that people would have to maintain.
- **Dates instead of instants** (SAP, Oracle and Dynamics 365): a date needs a time zone, and
  tenants have none yet; clients convert local midnight to UTC.
- **Refusing overlapping windows** (exclusion constraints, `WITHOUT OVERLAPS`): overlaps are
  legitimate here, and the best discount resolves them.
- Deferred: _Slab_ brackets, volume counted across lines (Oracle's "group of lines") and exclusive
  promotions.

## Consequences

- **Positive:** one query loads a tenant's rules for the engine; a breakdown is reproducible; the
  shape matches what managers know from ERPs.
- **Negative:** fixing a scope means deactivating the rule and creating another; windows are UTC
  instants; every effective rule of a tenant is loaded per pricing call, which suits hundreds of
  rules, not millions.
