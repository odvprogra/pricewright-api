# 0003. Money as an exact decimal with its currency, rounded half up

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

Prices, costs and quote totals are money. Binary floats cannot represent most decimal fractions (0.1
among them), so float arithmetic drifts. Distributors price small items below the cent (a screw at
0.0125 per unit), quotes must reconcile line by line, and tax must round the way customers and tax
authorities check it. Amounts also travel as JSON to clients written in other languages.

How others represent money:

- **Integer minor units** (Stripe's `amount`, Square): exact and simple, but no sub-cent prices;
  Stripe added `unit_amount_decimal` (up to 12 decimal places of the minor unit) for them.
- **Units and nanos** (Google's
  [`google.type.Money`](https://github.com/googleapis/googleapis/blob/master/google/type/money.proto)):
  exact to nine places, awkward in SQL and in JSON.
- **A decimal string and an ISO 4217 code** (PayPal's `{"currency_code", "value"}`): exact,
  readable, language-neutral.
- [Fowler's Money pattern](https://martinfowler.com/eaaCatalog/money.html): amount and currency
  always travel together, and arithmetic only mixes equal currencies.

[Dynamics 365 Sales](https://learn.microsoft.com/en-us/dynamics365/sales/decimal-precision-currency-pricing)
allows up to four decimals on list price, standard cost and price per unit. Commercial documents
round ties up: [Stripe Tax](https://docs.stripe.com/tax/calculating) "rounds tax amounts half up",
[Dynamics 365's "Normal" rounding](https://learn.microsoft.com/en-us/dynamics365/finance/localizations/global/tax-calculation-rounding-rules)
turns 987.345 into 987.35, and
[Council Regulation (EC) 1103/97](https://eur-lex.europa.eu/eli/reg/1997/1103/2001-01-01/eng) rounds
half-way results up. PostgreSQL's `round(numeric)` breaks ties away from zero.

## Decision

- **Domain.** A frozen `Money(amount: Decimal, currency)`; at most four decimal places and 14
  integer digits, so it always fits the column. Arithmetic (from M3) only between equal currencies.
- **Storage.** Amounts are `NUMERIC(18,4)`. Every table holding money has a `currency CHAR(3)`
  column, kept equal to the tenant's currency by a composite foreign key
  `(tenant_id, currency) → tenants (id, currency)`. The tenant's currency never changes.
- **JSON.** `{"amount": "12.3400", "currency": "USD"}`. The amount is a string, as
  [I-JSON (RFC 7493 §2.2)](https://www.rfc-editor.org/rfc/rfc7493#section-2.2) recommends for exact
  numbers, with four decimal places in responses. Requests may send up to four places: more is a
  422, never a silent rounding. A currency other than the tenant's is a 422.
- **Rounding points** (from M3 and M4): net unit prices to four places; line totals, tax and total
  to the currency's minor units (ISO 4217; two for USD). Tax is computed once, on the net subtotal
  (brief §4, rule 6). The mode is **half up, ties away from zero** (`ROUND_HALF_UP`), the same as
  PostgreSQL, so SQL reports (M10) reconcile with the application.

## Alternatives considered

- **Integer minor units:** cannot hold sub-cent unit prices without a second, decimal field.
- **Units and nanos:** exact, but every SQL query and every client would recombine two integers.
- **SAP's price unit** (a price per 100 or 1,000 pieces): avoids decimals, but every consumer must
  divide, and a wrong divisor is a silent hundredfold error.
- **Floats:** never; they cannot represent 0.1.
- **Half even (banker's rounding)**, the handbook's former rule: unbiased when many rounded values
  are summed, but not how invoices and tax are rounded, and quotes round each line only once.
- **Tax per line instead of per document:** equally common (Stripe and Dynamics 365 offer both); the
  brief chose the document level.
- **Currency only on the tenant:** less storage, but rows would not describe themselves, and quote
  and order snapshots need the currency anyway. Multi-currency (out of scope) becomes dropping a
  constraint rather than migrating data.

## Consequences

- **Positive:** exact arithmetic; totals that reconcile; one rounding rule in Python and SQL; any
  client parses the amounts without precision loss.
- **Negative:** clients must parse decimal strings, and responses show four places even for amounts
  rounded to cents (formatting is the client's job). The composite key ties every priced row to the
  tenant's currency.
