# 0008. Transactional outbox without a broker: PostgreSQL holds the events, the worker relays them

- **Status:** Accepted
- **Date:** 2026-10-11

## Context

Some changes must reach something outside their transaction: when a manager approves a quote, the
rep who submitted it gets an email with the PDF (brief §7); when a rep sends a quote, the customer
gets it. Sending from the use case either sends for a change that then rolls back, or loses the
message when the process dies after the commit: the dual-write problem. The audit trail is already
written in the same transaction as the change (ADR-0013) and stays so; it needs no delivery.

Practice:

- **The pattern.** A
  [transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html) stores
  messages in the database in the same transaction as the change, and a relay sends them; the relay
  "might publish a message more than once", so consumers are
  [idempotent](https://microservices.io/patterns/communication-style/idempotent-consumer.html).
  [AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)
  stores each event "with a timestamp and a sequence number", sends them in order and never for a
  rolled-back transaction.
- **The event's shape.**
  [Debezium's outbox](https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html)
  keeps an `id` (consumers deduplicate on it), an `aggregatetype`, an `aggregateid` (the key that
  keeps one aggregate's events in order), a `type` and a `payload`.
  [CloudEvents 1.0](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md) names the
  same envelope `id`, `source`, `type`, `subject`, `time` and `data`.
- **Queues in PostgreSQL.** `SELECT … FOR UPDATE SKIP LOCKED` lets several consumers take rows of a
  ["queue-like table"](https://www.postgresql.org/docs/current/sql-select.html) without blocking
  each other; [Brandur's job drain](https://brandur.org/job-drain) relies on a relay that "is unable
  to see jobs that aren't yet committed". [River](https://riverqueue.com/docs/job-retries) retries
  25 times with backoff `attempt^4 ± 10%`, then discards;
  [Oban](https://oban.hexdocs.pm/Oban.Worker.html) 20 times, exponential with jitter;
  [pg-boss](https://github.com/timgit/pg-boss/blob/master/docs/api/jobs.md) moves exhausted jobs to
  a dead-letter queue. Completed jobs are deleted after
  [24 hours (River)](https://riverqueue.com/docs/maintenance-services), seven days (pg-boss) or
  [a minute (Oban)](https://oban.hexdocs.pm/Oban.Pruner.html).
- **Jitter.** Exponential backoff with
  ["full jitter"](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/), a
  random wait up to the exponential ceiling, spreads retries best.

## Decision

- **An `outbox_events` table, written by use cases.** Approving, rejecting and sending a quote add
  an event to their unit of work, next to the audit event: the event commits with the change or not
  at all. New event types come with their consumer.
- **The envelope follows CloudEvents' names, without its SDK:** an id (UUIDv7), the tenant, a `type`
  named like audit actions (`quote.approved`), the aggregate's type and id, when it occurred, and
  thin `data` (the quote's number and who acted): consumers read the current record when they run. A
  webhook or a broker later would carry the same envelope.
- **Order.** A database identity, `position`, numbers events as they are inserted; changes to one
  aggregate are serialized by its compare-and-set (ADR-0012), so its events' positions follow the
  order of its changes.
- **The relay** (the worker, every second) takes up to ten due events in one transaction with
  `FOR UPDATE SKIP LOCKED`, each the earliest pending event of its aggregate, so two relays never
  hold the same event and an aggregate's events are delivered in order. It hands each to its
  consumer and records the outcome in the same transaction: a crash before the commit releases the
  events, which are delivered again (at least once). It claims again until nothing is due, so an
  aggregate's next event follows in the same run.
- **Failures.** A transient failure is retried after a random wait up to `5 s × 2^(attempts − 1)`,
  capped at an hour and never under a second (full jitter), for up to twelve attempts (about two and
  a half hours at most). A permanent failure (no consumer can ever succeed, such as an address the
  mail server refuses) and the last attempt make the event `dead`: kept with its last error for an
  operator, and no longer holding back its aggregate's later events.
- **Consumers are idempotent**; an email cannot be, so it is sent at least once, with a `Message-ID`
  derived from the event's id, which marks a duplicate as one. An event type without a consumer is
  delivered at once: emails are not sent for events written before their consumer existed.
- **The request id travels with the event**, stamped from the request like an audit event's, and the
  worker binds it while a consumer runs: its log lines join the API call's.
- **Delivered events are deleted after seven days** by the worker, once a day; dead ones stay until
  an operator fixes the cause and revives them: `pricewright-admin dead-letters` lists them,
  `pricewright-admin retry-dead-letters` (some ids, or `--all`) makes them pending again, with no
  attempts.

## Alternatives considered

- **A broker (Redis, RabbitMQ, Kafka):** a second system to run, and the outbox would still be
  needed to publish to it reliably; nothing here needs fan-out across services yet.
- **A queue library on PostgreSQL** (Procrastinate, the Python one): it defers jobs inside an
  external connection only with psycopg, not with asyncpg, so its job could not join our SQLAlchemy
  transaction; River, Oban and pg-boss are not Python.
- **Change data capture** (Debezium reading the WAL): no polling, but Kafka Connect to operate.
- **`LISTEN`/`NOTIFY` instead of polling:** lower latency; a one-second poll is enough for emails,
  and a notification would still need the poll as a fallback.
- **The audit trail as the outbox:** it records everything people change, while the outbox holds
  only what something reacts to, with delivery state the audit trail must never have (it is
  append-only).
- **Unlimited retries, or none:** an email outage would either retry forever or lose the email.

## Consequences

- **Positive:** a change and its email cannot disagree; no broker to run; relays scale out without
  coordination; per-aggregate order; failures and dead letters are visible rows.
- **Negative:** at-least-once delivery, so a rare duplicate email; up to a second of delay; a relay
  holds its transaction while a consumer runs (ten events at most, bounded by the consumers'
  timeouts); a dead event stops nothing else but waits for a person.
