# 0016. Catalog and customer master data: archived, never deleted, keyed by business codes

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

Products and customers are the master data that quotes and orders point to (M4, M6), that
`erp-mcp-server` searches, and that `ops-copilot` matches documents against (by SKU, by tax id or
name). Each needs a business key people and other systems use, a lifecycle that never breaks a
document pointing at it, and only the fields the product needs now.

How established systems model the same data:

- **Products.** Salesforce's `Product2` has a product code, a family, a quantity unit of measure and
  `IsActive`
  ([reference](https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_product2.htm));
  Odoo's product has an internal reference, a sales unit, a list price, a cost, a category and
  `active` as its soft delete; SAP's material number has up to 40 characters and a flat material
  group; Dynamics 365 Sales keeps list price and standard cost with up to four decimals. Stripe
  refuses to delete a product that has prices: it is archived (`active=false`) instead
  ([docs](https://docs.stripe.com/products-prices/manage-prices)).
- **Units.** UN/ECE Recommendation 20 codes units of measure, with Recommendation 21's package codes
  prefixed with `X`; Peppol and UBL documents use exactly this list
  ([Peppol](https://docs.peppol.eu/poacc/upgrade-3/codelist/UNECERec21/)).
- **Customers.** Every ERP keys a customer by an account number (SAP's customer number, the Dynamics
  365 customer account, Odoo's reference). Payment terms are configurable master data in SAP (up to
  three periods with cash discounts) and Dynamics 365 (days, months or "current month + N"); billing
  systems without that need keep the net days (Stripe's `days_until_due`). Stripe keeps tax ids as
  typed values on the customer, and a legal entity may hold several customer accounts.

## Decision

- **Never deleted, archived.** Products and customers have `is_active`; there is no `DELETE`.
  Inactive records stay readable and filterable, and quotes (M4) will refuse them.
- **Business keys, immutable.** A product's **SKU** (1–40 letters, digits, `.`, `_`, `/`, `-`) and a
  customer's **account number** are unique per tenant, ignoring case, and never change: other
  systems store them. A duplicate is a 409, which also makes creation retry-safe until the
  `Idempotency-Key` store arrives (M6).
- **Products** have a name, an optional flat category (hierarchies are additive later), one unit of
  measure from a curated set of UN/ECE codes, and a list price and unit cost as `Money` in the
  tenant's currency (ADR-0003). A list price below cost is allowed; the margin floor guard flags it
  on quotes (M3). Conversions between units (a box of 12) are out of scope.
- **Customers** have a name, an optional tax id (stored compact: upper case, without spaces, dots or
  hyphens, so `de 123.456-789` and `DE123456789` match; not unique), a tier (`standard`, `silver`,
  `gold`) that customer-tier discounts read (M3), and payment terms as net days
  (`payment_terms_days`, 0 = due on receipt, default 30). Cash discounts and period-based terms
  belong to invoicing, which is out of scope.
- **Search** is `q`, a case-insensitive "contains" on the record's codes and name within the
  tenant's rows (ADR-0014); exact lookups use `sku` or `tax_id`.

## Alternatives considered

- **Hard delete, or delete when unreferenced:** a quote or an audit event would point at nothing, or
  the answer would depend on history the caller cannot see.
- **Free-text units:** "ea", "each" and "EA" would all appear, and documents could not be matched to
  products by unit.
- **Payment terms as a configurable table:** the ERP model, with CRUD and validation for discounts
  that nothing in the product computes; net days migrate into it if invoicing ever arrives.
- **Tax id as the customer key:** optional for some customers and shared by branches.
- **A trigram index for search (`pg_trgm`):** worth it when one tenant's catalog reaches tens of
  thousands of rows; a few hundred per tenant are scanned faster than the index is maintained.

## Consequences

- **Positive:** documents never lose what they point to; other systems can rely on codes; units and
  terms match what purchase orders carry; retries cannot create duplicates.
- **Negative:** archived records accumulate and every list needs the `active` filter; a mistyped SKU
  or account number cannot be fixed in place (archive it and create the right one); the unit and
  tier sets need a migration to grow.
