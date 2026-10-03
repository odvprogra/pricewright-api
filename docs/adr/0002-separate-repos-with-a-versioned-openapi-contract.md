# 0002. Separate API and web repositories with a versioned OpenAPI contract

- **Status:** Accepted
- **Date:** 2026-10-02
- **Amended:** 2026-10-03 — the breaking-change check compares each pull request with `main`

## Context

Pricewright has an API and a web app, and two more clients: `erp-mcp-server` (MCP tools over the
API) and `ops-copilot` (document intake that drafts quotes and supplier price proposals). Each repo
is built from its own template and toolchain (Python here, Next.js for the web).

Industry practice treats the API description as the product's contract: define it first, never break
it silently, and version it with SemVer, using `0.y.z` while the design settles
([Zalando API guidelines](https://opensource.zalando.com/restful-api-guidelines/) rules
[#100](https://opensource.zalando.com/restful-api-guidelines/#100),
[#106](https://opensource.zalando.com/restful-api-guidelines/#106) and
[#116](https://opensource.zalando.com/restful-api-guidelines/#116)).

## Decision

We will keep `pricewright-api` and `pricewright-web` in separate repositories, integrated only
through the API's OpenAPI document:

- **Source of truth:** `openapi.json` is generated from the code, committed, and a test fails when
  it drifts (`just openapi` regenerates it).
- **Releases:** SemVer from Conventional Commits with
  [release-please](https://github.com/googleapis/release-please), which keeps `CHANGELOG.md`. Every
  GitHub release carries `openapi.json` as an asset. Before 1.0, a breaking change bumps the minor
  version.
- **Immutable releases:** a release is created as a draft, gets its `openapi.json`, then is
  published. After that its tag and assets cannot change, and GitHub records a release attestation
  ([immutable releases](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases)).
  A pinned contract stays exactly what the consumer reviewed.
- **Release PRs run CI:** release-please authenticates as a GitHub App. Events created with
  `GITHUB_TOKEN` do not start workflows
  ([GitHub docs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)),
  so its release PRs could never pass the checks that `main` requires. The release PR also bumps the
  version in `uv.lock` and in `openapi.json`, so it passes `uv sync --locked` and the drift test.
- **Consumers** (`pricewright-web`, `erp-mcp-server`, `ops-copilot`) pin a release and generate
  their client from its `openapi.json`. Upgrading is a reviewed version bump in the consumer.
- **Breaking changes:** from M2, when the first business endpoints exist, CI compares each pull
  request's spec with `main`'s using [oasdiff](https://github.com/oasdiff/oasdiff). A breaking
  change fails the check unless the pull request title marks it with `!` (`feat!:`), which makes
  release-please bump the version. Comparing with the latest release instead would keep failing
  every later pull request after an intended breaking change, until the next release.

## Alternatives considered

- **Monorepo with `api/` and `web/`:** atomic cross-cutting changes, but the service template would
  need a sub-directory mode, and the web app could import the spec from the working tree instead of
  a released version, bypassing the contract every other consumer follows.
- **API at the repository root with `web/` inside:** simplest to set up, but an asymmetric layout
  with the same contract bypass.
- **Spec served only at `/openapi.json`:** no versioned artifact; consumers would track a moving
  target.
- **Fine-grained personal access token for release-please:** quicker to create, but it expires, has
  to be rotated, and makes automated PRs look like the maintainer's own work.

## Consequences

- **Positive:** every consumer follows the same rule, contract changes are explicit and reviewable,
  and the two repositories deploy and release independently.
- **Negative:** a change that spans API and web needs two PRs (an API release, then a web bump). The
  GitHub App's private key is one more secret to manage. The release workflow lives in this
  repository until a second service releases the same way; then it moves to `engineering-standards`
  as a reusable workflow.
