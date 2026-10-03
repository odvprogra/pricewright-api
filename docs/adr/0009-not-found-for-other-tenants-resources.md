# 0009. 404 for another tenant's resources, 403 for missing permissions

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

Endpoints take ids (`/users/{id}`, later customers, quotes, orders). A caller can send an id that
belongs to another tenant, by mistake or to probe. Broken object-level authorization is the first
risk in the
[OWASP API Security Top 10](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization),
and the status code itself can leak information: a 403 says "this exists, but not for you".

HTTP allows hiding a forbidden resource: "a server MAY choose to send a 404 response if it wishes to
hide that the resource exists but access is forbidden"
([RFC 9110 §15.5.4](https://www.rfc-editor.org/rfc/rfc9110.html#name-403-forbidden)). GitHub does
exactly this for private repositories, "to avoid confirming the existence of private repositories"
([GitHub REST docs](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)).

## Decision

- **Another tenant's resource is a 404**, identical to an id that does not exist: same status, same
  Problem Details, except `instance`. It falls out of ADR-0006: repositories only ever see the
  caller's tenant, so the row is simply not found.
- **A missing permission is a 403** (`permission_denied`). Permissions are checked before any
  lookup, so a 403 never depends on whether an id exists, and it says nothing about another tenant.
  Inside one tenant, roles are not secret.
- Ids are random UUIDv7s (OWASP's advice), and the isolation suite asserts the 404 for every
  endpoint that takes an id.

## Alternatives considered

- **403 for another tenant's resource:** honest about the reason, but confirms that the id exists
  somewhere: an enumeration oracle.
- **404 for missing permissions too:** hides the endpoint's purpose from a user of the same tenant,
  who already knows it; it makes support harder for no security gain.

## Consequences

- **Positive:** an id leaks nothing across tenants, and clients handle "not yours" and "not there"
  the same way.
- **Negative:** support cannot tell from the status alone whether an id was mistyped or belongs to
  another tenant; logs keep the `request_id` to investigate.
