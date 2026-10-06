# 0022. Idempotency keys: stored with the change, per caller, replayed before preconditions

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

A client that loses the response to a request that creates something cannot tell whether it ran. The
handbook (§8) asks every non-idempotent `POST` that creates business records to accept an
`Idempotency-Key`, and the brief (§4, rule 5; §7) asks that retrying "convert to order" with the
same key returns the same order, never two. Today a retried `POST /quotes` leaves a second draft;
creations with a natural key answer a retry with a 409, and changes with `If-Match` with a 412.

Practice:

- The IETF draft
  [`draft-ietf-httpapi-idempotency-key-header-07`](https://www.ietf.org/archive/id/draft-ietf-httpapi-idempotency-key-header-07.html)
  (October 2025, expired without becoming an RFC, still the reference): the value is a Structured
  Field String; a fingerprint of the request may complete the key; a retry gets "the result of the
  previously completed operation, success or an error"; 409 while the original request is still
  being processed, 422 when the key is reused with another payload, 400 when a required key is
  missing; the expiry policy is published; keys are combined with attributes of the client the
  server knows.
- [Stripe](https://docs.stripe.com/api/idempotent_requests) saves the status code and body of the
  first request, 500s included, compares the parameters of every retry, keeps keys for at least 24
  hours, and saves nothing when validation fails or a concurrent request with the key is running.
- The
  [AWS Builders' Library](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
  scopes tokens to the caller, records the token and the mutation in one ACID operation, and answers
  a retry with a "semantically equivalent response": a retried `RunInstances` shows the instance as
  it is now, terminated if it was. [Google AIP-155](https://google.aip.dev/155) returns the previous
  response but "may return the current state of the resource instead".
- [RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-match): when a
  state-changing request "appears to have already been applied", the server may answer 2xx instead
  of 412, "perhaps because the prior response was lost".

## Decision

- **Where:** `Idempotency-Key` is optional on every `POST` that answers 201 Created, except issuing
  an API key, whose secret is shown once and never stored. The value has 1 to 255 visible ASCII
  characters without `"` or `\`; quoted, as the draft's String, or bare, as Stripe's clients send
  it. Anything else is a 422 (`invalid_idempotency_key`).
- **Scope:** the tenant and the caller (a user or a service account). The same value from another
  caller is another key.
- **Fingerprint:** a SHA-256 of the method, the path and the validated body as canonical JSON.
  Headers are left out, so a retry carrying a fresher `If-Match` is still the same request. Secrets
  are masked before hashing: a fast hash of a password would leak it, so a retry that only changes a
  new user's password counts as the same request.
- **Storage:** the table `idempotency_keys`, written in the same transaction as the change: the key,
  the fingerprint, the resource created (type and id), and when it was created and expires. Only
  completed creations are stored; a failure rolls back with everything else, so its retry is a new
  attempt.
- **Concurrency:** the first use of a key takes a transaction-level advisory lock without waiting; a
  request whose key is held gets a 409 (`idempotency_key_in_use`) at once.
- **Replay:** the same key with the same fingerprint gets 201, the same `Location` and the
  resource's current representation and `ETag`, marked `Idempotent-Replayed: true`, and records no
  audit event. The same key with another fingerprint is a 422 (`idempotency_key_reused`).
- **Order of checks:** authentication, then the request itself (a missing `If-Match` is still a 428,
  a malformed key a 422), then the permission, then the key, then the resource (404, 412) and the
  business rules. A replay ignores a stale `If-Match`, as RFC 9110 allows: the client never saw the
  version its own request created.
- **Expiry:** 24 hours. An expired record counts as absent and is replaced by the next request with
  its key; the worker (M5) purges them.

## Alternatives considered

- **Store the response body** (Stripe, the draft): replays are byte for byte, but the table would
  copy margins that only some callers may read (ADR-0017), keep the `ETag` and allowed actions of
  that moment and bodies of older API versions, and use cases would render HTTP inside their
  transaction.
- **Replay failures too** (the draft, Stripe): it matters when a failed request may have had side
  effects. Here a failure changes nothing, and replaying a 409 would force a new key after the
  client fixed what caused it.
- **Insert the key first and wait on its unique index:** no 409, but a retry holds a connection
  until the first request commits.
- **A "started" row committed first, with recovery points** (Stripe's Postgres design): needed when
  a request has external side effects, such as a payment; one local transaction has none.
- **Redis or a cache in memory:** not in the transaction of the change, and the brief keeps the
  service free of a broker.
- **Keys per tenant:** one caller could replay another's request and see a resource rendered for
  someone else's permissions.
- **A required key:** the draft allows it, but `If-Match` and unique constraints already prevent
  duplicates where it matters most (one order per quote), so it stays optional, as Stripe's is.

## Consequences

- **Positive:** a retried creation never creates twice and gets back what the first one created; a
  key commits or rolls back with its change; 409 and 422 tell a client whether to wait or fix a bug.
- **Negative:** a replay shows the resource as it is now, so an order cancelled since shows as
  cancelled; expired keys accumulate until the M5 purge; two keys whose 64-bit lock ids collide can
  get a needless 409; issuing an API key is not idempotent (a retry issues a second key, and the
  unused one is revoked).
