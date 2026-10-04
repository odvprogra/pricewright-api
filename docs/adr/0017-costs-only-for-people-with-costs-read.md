# 0017. Costs and margins only for people, behind `costs:read`

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

Products carry a unit cost (ADR-0016), and the pricing engine (M3) derives margins from it. Until
now `catalog:read` showed the cost to every reader of the catalog, service accounts included.
`erp-mcp-server` will give the catalog and price previews to an LLM that drafts quotes and messages
for customers, and a distributor's costs are among its most sensitive commercial data.

How others restrict it:

- **SAP** hides the cost condition (VPRS) from users by authorization, per step of the pricing
  procedure
  ([Note 105621](https://blogs.sap.com/2019/02/01/implementation-of-note-105621-authorization-check-for-condition-screen-in-s4-hana/)).
- **Salesforce CPQ** shows or hides cost and margin fields per user (field-level security and its
  [page security plugin](https://developer.salesforce.com/docs/revenue/cpq-plugins/guide/cpq-page-security-plugin.html));
  the REST API leaves out the fields a user cannot read.
- [OWASP API3:2023](https://api-security.owasp.org/editions/2023/en/0xa3-broken-object-property-level-authorization)
  asks for authorization per property: return only what the consumer should read.
- The [OWASP Top 10 for LLM applications (2025)](https://genai.owasp.org/llm-top-10/) lists
  sensitive information disclosure (LLM02) and excessive agency (LLM06): an LLM's tools get the
  least privilege they need.

Reps see margins on quote lines (brief §3), so the people who sell need the cost.

## Decision

- A permission **`costs:read`**: every role holds it, and it is reserved for people, so no service
  account can be granted it.
- Without it, a response **leaves out** the cost and whatever reveals it: a product's `unit_cost`
  now, and the margins of price previews (M3) and quote lines (M4). The flags that a line is below
  its margin floor, or that a quote needs approval, stay: they do not give the cost away, and an
  integration needs them to say what will happen.
- The property is omitted, not `null`, and the schema marks it optional with a description of who
  gets it. Writing a cost already needs `catalog:manage` (admins), who hold `costs:read`.

## Alternatives considered

- **Costs under `catalog:read`:** simplest, but every integration, and every LLM behind one, would
  read them.
- **A separate endpoint for costs:** authorization per route instead of per field, but clients that
  may see costs would make two calls per product, and lists a second query.
- **`null` instead of omitting:** a `null` cost reads as "no cost", and the field is required on a
  product.
- **Hiding costs from reps too:** some distributors do; the brief shows margins to reps so they can
  protect them. A tenant setting can add it later.

## Consequences

- **Positive:** an integration cannot leak costs, whatever it is granted; one rule, applied where
  responses are built.
- **Negative:** `unit_cost` became optional in the contract (a breaking change, made before 1.0 and
  before any consumer exists). Every response derived from costs must check the permission, and
  tests cover each one. A below-floor flag together with the floor's percentage still bounds the
  cost from below.
