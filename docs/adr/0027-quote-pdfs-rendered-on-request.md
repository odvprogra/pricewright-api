# 0027. Quote PDFs: an HTML template printed with WeasyPrint, rendered on request, never stored

- **Status:** Accepted
- **Date:** 2026-10-12
- **Amended:** 2026-10-29 — the brand's quotation, on a pinned pricewright-design release's tokens,
  with its fonts embedded (D-61)

## Context

The rep receives the approved quote as a PDF (brief §7), and a sent quote reaches the customer as
one. The brief suggests WeasyPrint "or similar"; the handbook (§14) wants PDFs and emails to read as
one product family, built from the same design tokens as the web app. A submitted quote never
changes (ADR-0019), and costs and margins never leave the company (ADR-0017).

Practice:

- **When the document is made.** Odoo prints a quotation whenever someone asks: its quotation report
  keeps
  [no attachment](https://raw.githubusercontent.com/odoo/odoo/17.0/addons/sale/report/ir_actions_report.xml),
  while reports can be stored once "for legal reasons", as invoices are. Stripe serves
  [`GET /v1/quotes/:id/pdf`](https://docs.stripe.com/api/quotes/pdf) "for a finalized quote";
  Dynamics 365 [creates a PDF](https://learn.microsoft.com/en-us/dynamics365/sales/create-quote-pdf)
  from the quote on request. Salesforce CPQ saves a quote document when it generates one, and Saleor
  [attaches](https://docs.saleor.io/developer/app-store/apps/invoices) a generated invoice to its
  order.
- **Engines.** [WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) (BSD)
  prints HTML and CSS; it needs Pango and HarfBuzz from the system: three Debian packages, or MSYS2
  on Windows. [Typst](https://pypi.org/project/typst/) ships as wheels with nothing to install, in a
  markup language of its own. ReportLab and fpdf2 draw pages from code. A headless Chromium prints
  HTML too, at the cost of a browser in the image.

## Decision

- **WeasyPrint and a Jinja2 template** (`infrastructure/templates/quote.html`, autoescaped), styled
  with CSS custom properties named after the handbook's semantic tokens; the brand (M12) sets their
  values. The emails will use the same template engine and tokens.
- **Rendered on request, never stored.** `GET /api/v1/quotes/{id}/pdf` (`quotes:read`) answers the
  quote as it stands, as an attachment named after its number (`NF-2026-000123-R2.pdf`); the email
  consumers render the same document. A submitted quote's numbers never change, so neither do its
  documents'.
- **What it shows:** the tenant, the number, the date it was submitted (or created), its validity
  and who prepared it; the customer's name, account and tax id; each line's SKU, description,
  quantity, list price, discount, net price and amount; subtotal, tax and total; the notes. Never
  costs or margins, whoever asks.
- **It says when it is not an offer:** a draft, a quote waiting for approval or rejected ("Draft,
  not an offer"), an offer past its date (expired by the clock, as ADR-0005), a cancelled or a
  superseded quote.
- **Rendering runs in a thread**, so it never blocks the event loop, and WeasyPrint is imported only
  to render: the service starts where Pango is missing, and only rendering fails.
- **The image installs** Pango, HarfBuzz, fontconfig and the DejaVu fonts, and builds the font cache
  at build time. Developers need Pango too (README).
- **The brand (amendment, M12).** The template is the quotation of pricewright-design's
  [`docs/brand/documents.md`](https://github.com/odvprogra/pricewright-design/blob/v0.1.0/docs/brand/documents.md),
  whose reference is code in that repository (`src/documents/quotation.ts`). Its styles are the
  `tokens.css` of a release pinned in `infrastructure/templates/brand/` (0.1.0), linked from the
  template; `just brand <version>` replaces it after checking it against the release's attestation
  (`gh release verify-asset`), and a test fails if the template uses a property the tokens do not
  define, a color of its own, or a release other than `DESIGN_RELEASE`. Source Serif 4 Semibold (its
  Text optical size, 18–26 pt, for the 18.75 pt "Quotation") and Public Sans Regular and SemiBold are
  committed as the OFL TrueType files their projects publish (Adobe's 4.005R, USWDS's v2.001) and
  loaded with `@font-face`; WeasyPrint subsets and embeds them. `@font-face` rules need a
  `FontConfiguration` passed to `write_pdf`
  ([WeasyPrint's first steps](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html));
  each render makes its own, so renders in their own threads never share one (about 0.3 s a page on
  a laptop). The facts beside
  the title are a CSS table, not the design's grid: WeasyPrint sized a grid inside a flex box too
  narrow and it ran past the margin. DejaVu stays in the image as the fallback for any glyph the
  brand fonts lack. The tenant's accent is the tokens' default until tenants have their own (M7,
  D-61).

## Alternatives considered

- **Render once per approved revision and store it** (Salesforce CPQ, Saleor's invoices): an exact
  record of what was sent, but a table of bytes and a "not ready yet" state; it becomes the right
  choice for legal documents such as invoices (out of scope, brief §10).
- **Typst:** no system libraries, but a second template language next to the emails' HTML, and the
  design tokens written twice.
- **ReportLab or fpdf2:** layout in code, far from the tokens and hard to review.
- **Headless Chromium:** faithful HTML, but a browser in the image for one document type.

## Consequences

- **Positive:** one template family for documents and emails; a PDF always matches the quote it
  comes from; nothing to store or clean up.
- **Negative:** system libraries in the image and on every developer's machine; a template change
  alters the look of past quotes' PDFs (never their numbers); each download renders again, a
  fraction of a second of CPU.
