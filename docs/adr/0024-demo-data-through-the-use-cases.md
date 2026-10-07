# 0024. Demo data: loaded through the use cases, reproducible from a seed and a date

- **Status:** Accepted
- **Date:** 2026-10-07

## Context

The presentable checkpoint needs demo data (brief §8): Northfield Supply with its catalog
([ADR-0025](0025-northfield-catalog-from-the-demand-dataset.md)), about 80 customers across tiers,
pricing rules of every kind, three reps, a manager and an admin, and about 150 quotes in every state
of their lifecycle; Larkspur Tool Co. with a small dataset. `just seed` loads it (handbook §3), the
same seed must give the same data, and the README gives reviewers credentials to sign in.

How established projects load demo data:

- **Rebuild, then load.** [`prisma migrate reset`](https://www.prisma.io/docs/cli/migrate/reset)
  drops the database, applies the migrations and runs the seed, after a confirmation;
  [Laravel's `migrate:fresh --seed`](https://laravel.com/docs/12.x/seeding) does the same; Rails'
  `db:reset` and `db:seed:replant` refuse protected environments such as production
  ([`ActiveRecord::ProtectedEnvironmentError`](https://blog.saeloun.com/2019/09/30/rails-6-adds-db-seed-replant-task-and-db-truncate-all/)).
  The [Rails guides](https://guides.rubyonrails.org/active_record_migrations.html) keep data out of
  migrations and ask for idempotent seeds (`find_or_create_by!`), for reference data that every
  environment needs.
- **Through the application.** Medusa
  [seeds by running its workflows](https://docs.medusajs.com/learn/fundamentals/custom-cli-scripts/seed-data)
  (`createProductsWorkflow`); Odoo loads demo data through its ORM.
- **Dates.** Odoo's demo data is dated relative to the day it is installed; Business Central's demo
  company works at a fixed "work date" instead.
- **Credentials.** Development and demo setups document their sign-in: GitLab's development kit
  ([`root` / `5iveL!fe`](https://docs.gitlab.com/development/contributing/first_contribution/configure-dev-env-gdk/)),
  Saleor's `populatedb --createsuperuser`
  ([`admin@example.com` / `admin`](https://saleor-fork.readthedocs.io/en/latest/gettingstarted/example-data.html)).
  The risk is [CWE-1392](https://cwe.mitre.org/data/definitions/1392), default credentials that
  reach a deployed product.

Two facts of this codebase shape the choice: an offer in flight acts as expired once its date
passes, by the real clock (ADR-0005), and ids are UUIDv7 drawn from the real clock in the domain.

## Decision

- **Through the use cases, as the tenants' people.** The seed (`pricewright.demo`, a layer between
  the adapters and the application in the import contracts) calls the same use cases as the API, as
  the person who would act: the admin builds the catalog, each rep enters the customers they manage,
  the manager keeps the pricing rules. Validation, prices, approvals, numbers and audit events are
  the real ones; no SQL is written around them.
- **A simulated clock and a seed.** Every use case gets a clock that the seed moves: the tenants go
  live 190 days before the as-of date (`--as-of`, default today in UTC), and the history ends before
  it. Dates are relative to that day, as Odoo's are, so offers are still open and the approval inbox
  still has requests on the day the data is loaded; with fixed dates they would all have expired
  within weeks. Draws come from `random.Random` seeded per purpose (`--seed`, default 2026), using
  only `random()`, whose sequence Python keeps across versions
  ([notes on reproducibility](https://docs.python.org/3/library/random.html#notes-on-reproducibility)).
  The same seed and as-of date give the same data; ids and the timestamps the database sets differ.
- **Fictional on purpose.** Customer names come from word lists of invented places and trades,
  people's emails use `.example` (reserved by [RFC 2606](https://www.rfc-editor.org/rfc/rfc2606)),
  and tax ids start with 00, a prefix the IRS
  [never assigns](https://www.irs.gov/businesses/small-businesses-self-employed/how-eins-are-assigned-and-valid-ein-prefixes).
- **Rebuild, then load.** `pricewright-admin seed` loads into a database without the demo tenants
  and refuses, changing nothing, when Northfield is already there. `just seed` migrates the local
  database down to nothing and up again, then loads, as `prisma migrate reset` does: running it
  again gives the same data, dated from the new day. Demo history cannot be upserted: audit events
  are append-only, numbers have no gaps, and quotes are never deleted.
- **Only in local and test environments.** The command refuses wherever `ENVIRONMENT` is staging or
  production, as Rails protects production. `just seed` asks first
  (`pricewright-admin seed --check`), before it migrates anything down, as Rails runs
  `db:check_protected_environments` before `db:reset`: a `.env` pointing at a deployed database
  stops the reset, not only the load.
- **One published passphrase.** Every demo user signs in with `pricewright demo`, listed in the
  README and printed by the command. It is not a secret: it guards fictional data that only exists
  where the command may run. A deployed demo (M8) will decide its own credentials.

## Alternatives considered

- **SQL or fixtures:** faster to load, but they would bypass the pricing engine, the approval rules
  and the audit trail they are meant to show, and drift from them.
- **Upserting seeds (Rails' `find_or_create_by!`):** right for reference data, not for a history of
  documents; and dates would go stale.
- **Fixed dates (a work date):** reproducible without an as-of date, but every offer would expire
  and the inbox empty a few weeks after the history's end.
- **Faker:** a dependency whose seeded output changes between releases
  ([its docs](https://faker.readthedocs.io/en/master/)), and whose company names can be real ones.
- **A random password per load, printed once** (GitLab's `initial_root_password`): nothing in the
  repository, but reviewers would have to copy it from a terminal, and it would change at every
  reset.
- **Deterministic ids:** an id generator injected into the domain; a redesign for a property only
  the seed needs.

## Consequences

- **Positive:** the demo exercises the real rules end to end; its tests double as tests of the use
  cases at volume; the data reads the same for every reviewer on the same day; a load is checked
  equal on the fakes and on PostgreSQL.
- **Negative:** loading takes seconds, not milliseconds; products and customers show the time they
  were loaded as `created_at` (the database sets it), while their audit events carry the simulated
  date; the same seed on another day shifts every date; anyone running the API locally with demo
  data knows the passphrase, by design.
