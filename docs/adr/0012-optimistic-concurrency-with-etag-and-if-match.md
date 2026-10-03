# 0012. Optimistic concurrency with ETag and If-Match

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

Mutable aggregates (tenant settings now, quotes in M4) are edited by several people. Without a
check, the second of two concurrent edits silently overwrites the first: the lost update problem.
Locking rows while a user edits a form is not an option over HTTP.

Industry practice has two consistent forms of optimistic concurrency:

- **HTTP-native:** the version travels as an `ETag`, and updates send it back in `If-Match`. When it
  no longer matches, the server **must** answer `412 Precondition Failed`
  ([RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#name-if-match)); a server that
  requires conditional updates answers `428 Precondition Required` when the header is missing
  ([RFC 6585 §3](https://www.rfc-editor.org/rfc/rfc6585.html#section-3)). Azure and Microsoft Graph
  work this way.
- **Version in the body:** a mismatch is `409` / `ABORTED`
  ([Google AIP-154](https://google.aip.dev/154)).

The handbook used to say "`If-Match` → 409", which breaks RFC 9110's "must". The quote line
endpoints were already planned with `If-Match`.

## Decision

- Every mutable aggregate carries a `version`, starting at 1. The repository saves with
  compare-and-set in one statement (`UPDATE … WHERE version = :read_version`), so of two concurrent
  saves exactly one wins, and the loser raises `StaleVersionError`.
- Responses send the version as a strong `ETag` (`"3"`) and in the body.
- Updates require `If-Match` with that ETag:
  - missing, or `*`: **428**, since `*` would match any version and defeat the check;
  - stale, weak (`W/"3"`) or not an ETag we issued: **412** (`stale_version`). The client reloads,
    shows what changed and lets the user decide (handbook §14.2).
- `409 Conflict` stays for conflicts with the resource's state, such as an invalid quote transition.
- `api/concurrency.py` implements the HTTP side once; aggregates reuse it.

## Alternatives considered

- **Version in the body, 409 on mismatch (AIP-154):** equally sound, but it gives up the standard
  headers that HTTP tooling and caches understand, and would reopen the quote line design.
- **`If-Match` optional:** clients that forget it would overwrite silently, the failure this exists
  to prevent.
- **Last write wins:** simplest, and wrong for quotes, where a manager may approve a version the rep
  has since changed.

## Consequences

- **Positive:** lost updates are impossible, and the behavior is plain HTTP that generated clients,
  proxies and the web app understand.
- **Negative:** every update costs clients one extra header, and they must keep the ETag from their
  last read. The web app needs a "someone else changed this" flow for 412 (M7).
