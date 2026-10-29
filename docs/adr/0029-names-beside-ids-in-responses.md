# 0029. Names beside ids in responses, looked up once per response

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

Records point at people, service accounts and customers by id: a quote's `customer_id` and
`created_by`, an approval request's `requested_by`. The screens that `pricewright-web` builds in M7
(pricewright-design's specs for the quotes list and the approval inbox) show names: the customer's
name and account number, and who built or submitted each quote. A sales rep cannot list users
(`users:manage` is an admin's), and asking for each row's customer and creator would cost a request
per row. The contract must stay additive (ADR-0002): `customer_id` and `created_by` keep their
shapes.

Practice:

- [GitHub's REST API](https://docs.github.com/en/rest/issues/issues#list-repository-issues) embeds a
  compact user object (login, id, avatar) in every issue and pull request it lists, always, so a
  list renders without further calls.
- [Stripe](https://docs.stripe.com/expand) returns ids and expands them into objects only when the
  request asks (`expand[]=data.customer`), up to four levels deep.
- [Zalando's guidelines](https://opensource.zalando.com/restful-api-guidelines/#158) say an API
  SHOULD allow optional embedding through an `embed` query parameter, and warn (rule 137) that
  "implementing embed correctly is difficult".
- [JSON:API](https://jsonapi.org/format/#fetching-includes) returns related resources in a separate
  `included` array when the client asks with `include`.

## Decision

- We will show names beside the ids they explain, always, in the responses whose screens need them:
  an actor object gains `name` (a user's full name or a service account's name), and a record with a
  bare `customer_id` gains a `customer` object with `id`, `name` and `account_number`. Emails are
  left out: nobody needs them to read a list.
- Names are today's, not a copy taken when the record was written: a renamed customer reads the same
  in every list. An order's `customer_name` stays the name as the order was placed (ADR-0023), a
  fact of the order, not a label.
- The use case looks the names up after its read, in the same unit of work, with one query per kind
  of record (`with_ids` on users, service accounts and customers), so a page costs the same few
  queries whatever its length. Users and service accounts are deactivated and customers archived,
  never deleted, so every id still has a name.
- First the quotes list and the approval inbox (`QuoteSummaryJson.customer`, `created_by.name`,
  `requested_by.name`); a quote's detail, its approvals' deciders and orders follow when their
  screens are built.

## Alternatives considered

- **`?expand=` (Stripe, Zalando rule 158):** a client asks only for what it shows, but every
  expandable field becomes optional in the schema, so the generated client must check for it on each
  read, and every endpoint needs tests for each combination. Three consumers that all want the names
  on these lists gain nothing from asking.
- **JSON:API `included`:** one copy of each related record per response, but a client must join the
  arrays itself, and no other response here has that shape.
- **Flat fields (`customer_name`, `created_by_name`):** simplest to add, but every reference grows
  two or three loose fields, and `customer_name` would mean today's name on quotes and the placed
  name on orders.
- **Names copied when the record is written:** no lookup on read, but a renamed customer or a user
  who fixed a typo in their name would show the old name in every list.
- **A people directory the web app caches:** reps would gain a user list they do not need, and every
  consumer would rebuild the same joins.

## Consequences

- **Positive:** a list renders from one response; the generated client sees required fields; the
  contract only grows.
- **Negative:** each response that names records runs up to three more queries, and the use cases
  that return them also return the names. A name an id cannot find is a bug, so it fails loudly.
