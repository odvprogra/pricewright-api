# 0028. Emails: the rep hears about decisions, the customer receives sent quotes, the worker sends

- **Status:** Accepted
- **Date:** 2026-10-14
- **Amended:** 2026-10-29 — the brand's emails, with values copied from the pinned tokens (D-61)

## Context

When a manager approves a quote, the rep receives an email with the PDF (brief §7); when a rep sends
a quote, the customer should receive it. Customers had no email address, so nothing could reach
them. Approving, rejecting and sending already leave an event in the outbox (ADR-0008), and quote
PDFs are rendered on request (ADR-0027).

Practice:

- **Who receives a sent quote.** Odoo's "send quotation" template is addressed to the customer
  (`partner_to` is the order's partner), sent from the salesperson (`email_from`) and attaches the
  quotation report
  ([template](https://raw.githubusercontent.com/odoo/odoo/17.0/addons/sale/data/mail_template_data.xml)).
  Salesforce CPQ addresses the quote's primary contact
  ([Trailhead](https://trailhead.salesforce.com/content/learn/modules/quote-templates-in-salesforce-cpq/structure-a-customized-quote-template)),
  and Stripe a customer's `email`. Contacts per customer (SAP, Salesforce, Dynamics 365) are richer,
  and a larger change.
- **Sending on someone's behalf.** A message whose `From` is the rep's own domain but which leaves
  another domain's servers fails DMARC alignment; platforms send from their own domain and put the
  person in `Reply-To` (RFC 5322 §3.6.2).
- **Duplicates.** At-least-once delivery (ADR-0008) can send an email twice; a `Message-ID` derived
  from the event (RFC 5322 §3.6.4) identifies a duplicate as one message.
- **Local mail.** [Mailpit](https://mailpit.axllent.org/docs/install/docker/) catches SMTP on port
  1025 and shows it on port 8025, with a REST API for tests; cookiecutter-django offers a mail
  catcher as its own option, apart from its worker.

## Decision

- **Customers get an optional `email`**, a single address where the quotes sent to them go (Odoo's
  partner email, Stripe's `customer.email`). Without one, nothing is emailed: the rep downloads the
  PDF and delivers it.
- **Who receives what:**
  - `quote.approved`: the rep who submitted the quote (a person), with the PDF;
  - `quote.rejected`: the same rep, with the manager's comment saying what to change;
  - `quote.sent`: the customer's email, with the PDF and the rep in `Reply-To`.
  - An integration that submitted a quote has no mailbox: its quote's decisions email no one.
- **From the platform, on the tenant's behalf:** `From` is the configured sender address with the
  tenant's name as its display name; replies go to the rep.
- **The worker sends,** as the outbox's consumers, over SMTP with the standard library (`smtplib`
  and `email.message`, run in a thread): STARTTLS and credentials come from settings. The mail
  server's permanent refusals (5xx) make the event dead at once; anything else is retried
  (ADR-0008). Each email's `Message-ID` is derived from its event.
- **Templates** are Jinja2, HTML and plain text, sharing the PDF's tokens (ADR-0027).
- **The brand (amendment, M12).** The three emails follow pricewright-design's
  [`docs/brand/documents.md`](https://github.com/odvprogra/pricewright-design/blob/v0.1.0/docs/brand/documents.md)
  (reference: `src/documents/emails.ts`): one column of at most 600 px on presentation tables with
  `lang` and `dir`, the facts as a data table with row headers, the tenant's rule and name, a serif
  heading without numbers (Georgia, the stack's fallback, sets old-style figures), a hidden preview
  line, a manager's comment as a quotation, light only, and a plain-text part with the same blocks.
  Mail clients drop `<style>` and do not resolve `var()`, so every style is inline and every value
  comes from the pinned release's `tokens.json` (`EmailStyle`, sizes in pixels); no web fonts, since
  Gmail and Outlook for Windows ignore `@font-face`
  ([caniemail](https://www.caniemail.com/features/css-at-font-face/)). Customers read formal
  sentences, reps the app's voice; the decisions' subjects are unchanged.
- **Locally, Mailpit** runs in docker compose; it lives in this repository, the only one that sends
  email.

## Alternatives considered

- **Contacts per customer** (a sub-resource the quote picks one of): the B2B norm in ERPs and CRMs,
  but a sub-resource, its own permissions and a choice at sending; a single address covers the demo
  and can grow into contacts.
- **Only internal emails in M5:** the rep would hear about approvals, but `send` would stay a status
  that reaches no one.
- **The rep's address in `From`:** fails DMARC when the platform's servers send it.
- **An async SMTP library (aiosmtplib):** a dependency for a handful of messages a minute, when a
  thread with the standard library suffices.
- **A transactional email API (SES, Postmark):** better deliverability in production, behind the
  same consumer; SMTP works with all of them and with Mailpit.

## Consequences

- **Positive:** the approval reaches the rep and the sent quote the customer, with the same PDF the
  API serves; an email goes out if and only if its change committed; duplicates are identifiable.
- **Negative:** one address per customer; a rare duplicate email; customers without an address get
  nothing automatically; email addresses are personal data, kept with the customer and in its audit
  trail.
