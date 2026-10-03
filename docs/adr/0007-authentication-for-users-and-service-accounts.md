# 0007. Authentication for users and service accounts, without an external identity provider

- **Status:** Accepted
- **Date:** 2026-10-03

## Context

People sign in with an email and a password; `erp-mcp-server` and `ops-copilot` call the API as
service accounts with scoped API keys (brief §5). The web app keeps tokens in httpOnly cookies
behind its own backend (handbook §14.4), and only this API verifies the tokens it issues. The demo
has to run with `docker compose up`, without third-party accounts.

## Decision

- **Identity.** A user belongs to one tenant and signs in with an email that is unique across all
  tenants, compared case-insensitively, so login never asks for the tenant (Salesforce applies the
  same rule to usernames).
- **Passwords.** argon2id
  ([OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)) with
  argon2-cffi's defaults (RFC 9106's second profile), rehashed at login when the parameters change
  and computed off the event loop. NFKC-normalized, 15 to 128 characters, no composition rules, and
  an account locks after 100 consecutive failures
  ([NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html) §3.1.1.2, §3.2.2). An unknown
  email costs the same work as a wrong password, so timing does not reveal which emails exist.
- **Access tokens.** 15-minute JWTs signed with HS256 (PyJWT), typed `at+jwt`
  ([RFC 9068](https://www.rfc-editor.org/rfc/rfc9068.html)), verified against a fixed algorithm,
  issuer and audience ([RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html)). The secret comes
  from the environment; a development default is refused outside local and test environments.
- **Refresh tokens.** Opaque random values stored as SHA-256 hashes and rotated on every use.
  Presenting a rotated token revokes its whole family
  ([RFC 9700 §4.14.2](https://datatracker.ietf.org/doc/html/rfc9700#section-4.14.2)). They expire
  after 14 days unused or 30 days in total.
- **Service accounts.** Each holds scopes, a subset of the non-administrative permissions. Its API
  keys look like `pwk_<40 base62><CRC32 checksum>`, after
  [GitHub's token format](https://github.blog/engineering/platform-security/behind-githubs-new-authentication-token-formats/),
  so secret scanners can recognize them; they are stored as SHA-256 hashes, shown once, and can
  expire or be revoked. Keys and JWTs both travel as `Authorization: Bearer`.
- **Authorization.** Permissions are `resource:action`. A role maps to a set of permissions; the
  authenticated principal (user or service account) carries its tenant and permissions, and every
  endpoint requires one permission.

## Alternatives considered

- **External identity provider (Keycloak, Auth0, Cognito):** the right call for SSO and MFA, but one
  more service to run or a vendor account for a demo. The principal abstraction leaves room to add
  OIDC later.
- **Server-side sessions:** every request would read the session store; the brief asks for
  short-lived JWTs, and the web backend already holds them in cookies.
- **RS256 or EdDSA:** only worth it when other services verify the tokens; none does.
- **pwdlib:** algorithm agility we do not need for a single algorithm.

## Consequences

- **Positive:** no external dependency; every parameter is traceable to a standard; rotating the JWT
  secret only forces a refresh, because refresh tokens live in the database.
- **Negative:** this service owns security-sensitive code (the cryptography comes from libraries,
  and every rule has tests). A role change or deactivation reaches existing access tokens within 15
  minutes. Deferred: MFA, rate limiting by IP, a breached-password blocklist, SSO.
