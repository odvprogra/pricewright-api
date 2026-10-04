# 0014. List queries: whitelisted filters and sort, keyset cursors bound to the query

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

From M2 every list (catalog, customers, the audit trail) needs filters and a choice of order, and
the handbook (§8) already asks for cursor pagination and whitelisted parameters. Consumers differ:
the web app shows sortable tables whose state lives in the URL (handbook §14.2), `erp-mcp-server`
searches products and customers by text, `ops-copilot` looks up an exact SKU or tax id.

Practice:

- [Zalando's rule 137](https://opensource.zalando.com/restful-api-guidelines/#137) names the
  conventional parameters: `q` for search, `sort` with `+`/`-` prefixes, `cursor` and `limit`;
  [rule 160](https://opensource.zalando.com/restful-api-guidelines/#160) prefers cursors to offsets.
- [Google AIP-132](https://google.aip.dev/132) adds ordering only where there is a need, and
  [AIP-158](https://google.aip.dev/158) keeps page tokens opaque and rejects a token sent with
  different parameters.
- The seek method
  ([Use The Index, Luke](https://use-the-index-luke.com/sql/partial-results/fetch-next-page))
  continues after the last row's sort key plus a unique tie-breaker, using a row-value comparison
  that an index serves; offsets get slower and skip or repeat rows when data changes.

The default collation of the `postgres:18-alpine` image sorts like C (`Apple, Zeta, apple`), so
names would not sort the way people read them.

## Decision

- **Filters** are explicit query parameters per list, whitelisted and typed: exact matches (`sku`,
  `tax_id`, `tier`, `active`, ids) and, where people search, `q`: a case-insensitive "contains" over
  the list's text fields. Unknown parameters are ignored, as HTTP clients expect; invalid values are
  a 422.
- **Sort** is `sort=<field>` or `sort=-<field>`, one field from the list's whitelist, with a
  documented default. Text fields sort with the Unicode collation (`COLLATE "unicode"`, ICU's root
  locale): case and accents order the way people read them, the same on every server. Lists only
  offer sorts that an index serves.
- **Keyset pagination.** A page continues after the last row's `(sort value, id)`; the UUIDv7 id
  breaks ties and gives creation order. The cursor is opaque base64url holding that position and a
  fingerprint of the query (filters and sort). A cursor sent with another query is a 422
  (`invalid_cursor`), never a page of a different list.
- **No totals.** Counting a filtered list costs as much as reading it; clients get `next_cursor`.
- The audit trail has a fixed order, newest first; its filters are resource, actor and action.

## Alternatives considered

- **Offset pagination:** simple and allows jumping to page N, but slow on deep pages, and rows shift
  between requests. Tables in this product page forward.
- **A filter language** (AIP-160, OData `$filter`): powerful, but a parser, a security surface and
  indexes for queries nobody needs yet.
- **Sorting by several fields:** each combination needs its own index to stay fast; one field plus
  the id covers the screens in the brief.
- **Signed cursors:** a tampered cursor can only move within the caller's own tenant's list, so a
  signature would add a key to rotate and protect nothing.
- **`lower()` with the default collation:** fixes case only, not accents, and still depends on the
  server's C library.

## Consequences

- **Positive:** stable pages under concurrent writes, every list query served by an index, URLs that
  capture a view, and misuse of a cursor caught instead of silently wrong.
- **Negative:** no "jump to page N" and no total count; adding a sortable field means adding an
  index; PostgreSQL must be built with ICU (the official images and managed services are).
