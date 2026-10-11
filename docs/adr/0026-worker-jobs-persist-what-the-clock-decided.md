# 0026. Worker jobs: persist what the clock decided, find across tenants, act as the system

- **Status:** Accepted
- **Date:** 2026-10-11
- **Amended:** 2026-10-11 — the worker also purges expired idempotency keys (ADR-0022)

## Context

An offer in flight past its `valid_until` already acts as expired, before any job runs (ADR-0005).
It keeps its last status, though: `GET /quotes?status=expired` finds nothing, a report would count
it as sent, and its approval request stays pending in the table. Decision D-08 left a periodic job
to persist `expired`; the `expire` transition is in the table since M4. Milestone M5 brings the
worker process (the template's ADR-0008), and more jobs follow: purging expired idempotency keys
(ADR-0022) and delivered outbox events (ADR-0008).

Two questions come with a job. Nobody in particular acts, yet every change appends an audit event
naming its actor (ADR-0013), and actor types are a closed set (ADR-0015). And a job must find work
in every tenant, while units of work are bound to one tenant and only authentication reads across
tenants (ADR-0006).

Practice:

- **Automatic expiry.** Stripe's open quotes
  ["automatically cancel when they reach the expiration date"](https://docs.stripe.com/quotes/overview).
- **Who acted.** A Stripe event's `request.id` is null when
  ["the event was automatic"](https://docs.stripe.com/api/events/object). Salesforce runs a
  scheduled flow as a configured workflow user or, by default, the
  [Automated Process user](https://admin.salesforce.com/blog/2022/your-guide-to-determining-the-flow-running-user-and-its-execution-context);
  Dataverse runs its own services as
  [system application users](https://learn.microsoft.com/en-us/power-platform/admin/system-application-users).
- **Scheduling.** [River's periodic jobs](https://riverqueue.com/docs/periodic-jobs) are enqueued by
  an elected leader; runs missed while it is down are skipped, and `RunOnStart` covers restarts.

## Decision

- **Jobs are use cases the worker runs** (`application/housekeeping.py`). The expiry job runs when
  the worker starts and every 10 minutes; a failed run is logged and tried again at the next turn.
- **Find across tenants, change within one.** A job asks `uow.housekeeping` what is due in every
  tenant and gets ids only. Each record is then changed in a unit of work bound to its tenant,
  through that tenant's repositories. `housekeeping` is the second port that crosses tenants, after
  authentication's lookups (ADR-0006).
- **Purges across tenants.** Expired technical records that no tenant reads any more are deleted
  across tenants, in batches of a thousand per transaction: idempotency keys every hour (ADR-0022).
- **Expiry.** Every offer in flight (pending approval, approved or sent) whose `valid_until` has
  passed becomes `expired` (`Quote.expire`), and its pending approval request is withdrawn. The
  status changes at the run's time. Each offer has its own unit of work and saves with
  compare-and-set (ADR-0012). A run looks at each offer once, by id, so it always ends.
- **No leader.** Two runs at once expire each offer once: one saves, the other finds the version
  stale and skips it. A run missed while the worker is down is done by the next one.
- **The system as an actor.** The audit trail names it `actor_type: system`, with the nil UUID
  ([RFC 9562 §5.9](https://www.rfc-editor.org/rfc/rfc9562#section-5.9)) as `actor_id`. Business
  records keep naming people and integrations only: the database accepts `system` in `audit_events`
  alone, and quote and order responses keep `user | service_account`. Adding `system` to the audit
  events' `actor_type` is a breaking change for clients (ADR-0015), released with a `!`.
- **Each run has an id**, bound as the request id: the worker's log lines and the audit events of
  that run carry it, so one joins the other.

## Alternatives considered

- **A leader per job** (an advisory lock or an election): saves duplicate reads; it would hold a
  connection for the whole run or need an election, and correctness does not depend on it.
- **Expire lazily when a quote is read:** writes on reads, and lists and reports would still show
  stale statuses.
- **SQL scheduled in the database (`pg_cron`):** it would bypass the transition table, the approval
  requests and the audit trail.
- **A "system" service account per tenant**, like Dataverse's users: no contract change, but it
  would appear among the tenant's integrations and need protection from edits and API keys.
- **No request id for automatic changes** (Stripe): audit events of a run could not be joined to the
  worker's log lines.
- **Open-ended actor types** (ADR-0015): the set is fixed by the domain; a value added here is a
  deliberate, announced change.

## Consequences

- **Positive:** `status=expired` lists and reports are right within ten minutes of midnight UTC;
  expired offers' approval requests leave the inbox by their status; replicas of the worker need no
  coordination; an automatic change says so in the audit trail.
- **Negative:** up to ten minutes pass before an expiry is stored (the clock already decides what an
  offer allows); clients reading audit events must accept `system`; each run reads across tenants,
  which a partial index of offers in flight keeps small.
