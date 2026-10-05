# 0021. Quote numbers: the tenant's prefix, the year and a counter per tenant and year

- **Status:** Accepted
- **Date:** 2026-10-05
- **Amended:** 2026-10-05 — orders are numbered the same way, in a series of their own with the
  tenant's order prefix

## Context

Every quote needs a number people can read out and customers can quote back: `NF-2026-000123` for
Northfield Supply, revisions as `NF-2026-000123-R2` (brief §3, decision D-07; prefixes `NF` and
`LT`, D-14). Numbers are per tenant, so one tenant's numbering must not reveal another's activity.

What established systems do:

- **PostgreSQL sequences** never hand back a number from an aborted transaction, so they
  ["cannot be used to obtain gapless sequences"](https://www.postgresql.org/docs/current/functions-sequence.html).
- **SAP number ranges:** many document types have no legal constraint and are buffered in memory,
  which leaves gaps; invoices must be numbered without gaps in several countries
  ([SAP](https://blogs.sap.com/t5/technology-blogs-by-sap/buffering-number-ranges-and-legal-framework/ba-p/12953573)).
- **Dynamics 365 number sequences** are continuous (no gaps, for legal requirements, at a
  performance cost) or non-continuous, and can be scoped by company and fiscal period
  ([TechTalk](https://learn.microsoft.com/en-us/dynamics365/guidance/techtalks/finance-operations-continuous-number-sequence-performance-improvements)).
  Dynamics 365 Sales numbers quotes with a configurable prefix (`QUO` by default).
- **Stripe** numbers quotes `QT-<customer prefix>-0001-1`, the last part being the revision
  ([quotes](https://docs.stripe.com/quotes/overview)).

Quotes are not invoices: no law asks their numbers to be gapless. But reps notice a missing number
and ask about it.

## Decision

- **Format:** `{prefix}-{year}-{sequence}`, the sequence padded to six digits (more digits after
  999,999). The prefix is the tenant's setting, 2 to 5 upper-case letters or digits (default `QUO`);
  the year is the UTC year of creation until tenants have time zones (ADR-0018).
- **Revisions share the number** and are told apart by their revision; the second and later show as
  `-R2`, `-R3`. The number is stored when issued and never rebuilt, so a new prefix changes only new
  quotes.
- **A counter per tenant and year** (`quote_number_counters`), advanced with one
  `INSERT … ON CONFLICT DO UPDATE … RETURNING` in the transaction that creates the quote. The upsert
  locks the counter row until commit: concurrent quote creations in the same tenant wait for each
  other, and a rollback takes the number back. Numbers are issued when a draft is created, as SAP
  and Dynamics 365 do, and quotes are cancelled, never deleted, so the series has no gaps.
- **Orders (amendment, M6):** numbered alike, `{order prefix}-{year}-{sequence}`
  (`ORD-2026-000045`), in a series of their own, as SAP assigns a number range per document type and
  Dynamics 365 Sales a prefix per record type (`QUO`, `ORD`, `INV`;
  [auto-numbering](https://learn.microsoft.com/en-us/power-platform/admin/change-auto-number-prefix-contract-case-article-quote-order-invoice-campaign-category-knowledge-articles)).
  The counter table becomes `document_number_counters`, keyed by tenant, series (`quote`, `order`)
  and year; existing quote counts carry over. The tenant's `order_prefix` defaults to `ORD` and must
  differ from its quote prefix, so a number never names a quote and an order at once. An order's
  number is issued when it is converted (ADR-0023).

## Alternatives considered

- **One PostgreSQL sequence per tenant and year:** gaps after every rollback, and creating sequences
  at runtime for each new tenant and year.
- **One sequence for everyone:** numbers would jump with other tenants' activity and leak it.
- **Numbers at submission** (Stripe numbers quotes at finalization): drafts would have no number to
  refer to, and abandoned drafts would not consume one; reps quote drafts to each other too.
- **A UUID or random code:** unique without coordination, but nobody reads one over the phone.

## Consequences

- **Positive:** readable, per-tenant numbers without gaps; the counter is ordinary data, so seeds
  and tests control it.
- **Negative:** creating quotes is serialized per tenant for the length of the creating transaction
  (fine for B2B volumes; keep that transaction short); the year boundary follows UTC, so a quote
  created on New Year's Eve in the Americas may get next year's number.
