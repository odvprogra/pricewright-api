# 0013. Append-only audit events, written in the same transaction as the change

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

Pricewright promises a full audit trail: who changed a price, a customer, a role or an API key,
when, and from what to what (brief §1 and §3). Support, security reviews and disputes over a quote
all start from that question. The brief first listed "audit projections" as a consumer of the
transactional outbox (M5), but M2 already changes catalog and customer data, and M1 left admin
actions (roles, API keys) unaudited.

What an audit record holds is well established. NIST SP 800-53
[AU-3](https://csf.tools/reference/nist-sp-800-53/r5/au/au-3/) asks for the type of event, when and
where it happened, its source, its outcome and the identities involved; the
[OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
lists the same when / where / who / what.
[GitHub's audit log](https://docs.github.com/en/organizations/keeping-your-organization-secure/managing-security-settings-for-your-organization/audit-log-events-for-your-organization)
records an `action` such as `repo.create`, the actor and the time;
[WorkOS](https://workos.com/docs/audit-logs) names events `resource.verb` with an actor and targets;
Salesforce's field history keeps each field's old and new value. The
[audit logging pattern](https://microservices.io/patterns/observability/audit-logging.html) records
user actions in a database; its cost is audit code next to the business logic.

## Decision

- **Same transaction.** A use case appends an `AuditEvent` to its unit of work, next to the change
  it records. Both commit or neither does, and the event is readable at once. The M5 outbox may
  publish events to other consumers; it is not their source of truth.
- **Content.** Tenant; time; actor (`user` or `service_account`, and its id); action
  (`resource.verb` in the past tense, such as `user.updated`); resource type and id; `changes`, the
  fields that changed as `{field: [before, after]}`; and the request ID of the API call, which joins
  the event to its log lines. Never secrets or their digests.
- **Scope.** Changes to tenant settings, users, service accounts and API keys (from M1), and to the
  catalog and customers (M2). Sign-ins stay in the structured logs: they are high-volume and are not
  changes to business records.
- **Append-only, enforced by the database.** A trigger rejects every `UPDATE` and `DELETE` on
  `audit_events`, whoever runs it. Actor and resource ids have no foreign keys: they point to
  several tables, and the history must outlive what it describes.
- **Reading.** `GET /api/v1/audit-events`, newest first, filtered by resource, actor or action;
  admins only (`audit:read`), not grantable to service accounts.

## Alternatives considered

- **Outbox-fed projection (M5):** decouples the write, but the trail would lag, depend on the worker
  running, and could not exist before M5.
- **Database triggers that diff every row:** nothing to forget in a use case, but triggers do not
  know the actor or the request, and they record storage changes instead of business actions.
- **Event sourcing:** the events are the state, so the trail is complete by construction; a large
  architectural shift for a CRUD-heavy milestone.
- **Structured logs only:** already there, but logs rotate, are not tenant-scoped and cannot be
  queried from the API.

## Consequences

- **Positive:** every audited change has exactly one event, with its actor and its request; history
  per record, per person or per action is one indexed query; tampering through the application or
  SQL is refused.
- **Negative:** each use case must record its event (the tests check every one); the table grows
  without bound until a retention policy exists; `TRUNCATE` is still possible for the table's owner,
  so production should run the application under a role that does not own the table.
