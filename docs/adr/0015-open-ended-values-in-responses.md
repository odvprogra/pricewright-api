# 0015. Open-ended values in responses, closed enums in requests

- **Status:** Accepted
- **Date:** 2026-10-03
- **Amended:** 2026-10-05 — a quote's allowed actions and approval reasons are open-ended; quote
  statuses stay an enum

## Context

Some response fields take values from a set that grows with the product: audit actions and resource
types (every milestone adds some) and service account scopes (M2 adds catalog and customer
permissions). Declared as `enum` in the OpenAPI spec, every new value is a breaking change for
oasdiff (`response-property-enum-value-added`), and rightly so: a client generated from the spec may
reject a response with a value it does not know.

[Zalando's rule 112](https://opensource.zalando.com/restful-api-guidelines/#112) uses an open-ended
list of values, documented with `examples`, for enumerations that will grow;
[rule 108](https://opensource.zalando.com/restful-api-guidelines/#108) asks clients to tolerate
compatible extensions such as new values.

## Decision

- A response field whose set of values will grow is a `string`, with today's values listed in
  `examples` and a description telling clients to handle unknown values: an audit event's `action`
  and `resource_type`, a service account's `scopes` (an enum until M2 started adding permissions), a
  product's `unit`, a pricing rule's `kind` (kinds such as exclusive promotions are expected), a
  price breakdown step's `stage`, and a quote's `allowed_actions` (orders add one in M6) and
  `approval_reasons`.
- Fixed sets stay `enum` in responses: roles, customer tiers, actor types, and quote statuses, which
  the lifecycle fixes (ADR-0005; `converted` is listed before orders exist). Adding a value to one
  of those is a breaking change, marked with `!`.
- Requests keep `enum` everywhere, so invalid input is a 422; adding an accepted value to a request
  never breaks a client.

## Alternatives considered

- **`enum` everywhere, `!` on every addition:** honest about strict clients, but each milestone
  would bump the contract as breaking for an expected addition, and the changelog would cry wolf.
- **`x-extensible-enum`:** the older vendor extension for the same idea; Zalando moved to
  `examples`, which is plain JSON Schema.

## Consequences

- **Positive:** the contract grows without breaking clients, and oasdiff still catches real breaks.
- **Negative:** generated clients see plain strings for these fields, so they lose exhaustiveness
  checks and must keep a default branch. Clients generated before `scopes` became open-ended must be
  regenerated before they meet a new scope.
