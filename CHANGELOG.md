# Changelog

## [0.3.0](https://github.com/odvprogra/pricewright-api/compare/v0.2.0...v0.3.0) (2026-10-04)


### Features

* add the Money value object and record how money is represented ([#26](https://github.com/odvprogra/pricewright-api/issues/26)) ([d0bd98e](https://github.com/odvprogra/pricewright-api/commit/d0bd98e869041b9651fa0237055735a29d9c0934))
* document service account scopes as open-ended values ([#31](https://github.com/odvprogra/pricewright-api/issues/31)) ([c9855c1](https://github.com/odvprogra/pricewright-api/commit/c9855c1f3f523bb69958a1278778e30d7a413d40))
* let admins manage product categories ([#32](https://github.com/odvprogra/pricewright-api/issues/32)) ([d5bebe4](https://github.com/odvprogra/pricewright-api/commit/d5bebe433ae0c2bb7eb49ebfd292c41fdef7bbf4))
* let admins manage the product catalog over the API ([#34](https://github.com/odvprogra/pricewright-api/issues/34)) ([9ea2860](https://github.com/odvprogra/pricewright-api/commit/9ea286060b5401f51680a801c79f54cf5378f7e2))
* let admins read the audit trail ([#30](https://github.com/odvprogra/pricewright-api/issues/30)) ([5c1026a](https://github.com/odvprogra/pricewright-api/commit/5c1026aeb1322af2044dbbcb67fe014a2f8d3be2))
* let sales teams manage customers over the API ([#36](https://github.com/odvprogra/pricewright-api/issues/36)) ([5eaae59](https://github.com/odvprogra/pricewright-api/commit/5eaae592266c16b653abd57d7e72e7df180dc853))
* record admin actions in the audit trail ([#29](https://github.com/odvprogra/pricewright-api/issues/29)) ([cdbd63a](https://github.com/odvprogra/pricewright-api/commit/cdbd63a4542df2cfb690c010aaabb984f49b0ef0))
* store append-only audit events ([#28](https://github.com/odvprogra/pricewright-api/issues/28)) ([9a51b75](https://github.com/odvprogra/pricewright-api/commit/9a51b755ab5d1e1a320473797e0f613cd0df56cb))
* store customers keyed by account number ([#35](https://github.com/odvprogra/pricewright-api/issues/35)) ([cb1be8c](https://github.com/odvprogra/pricewright-api/commit/cb1be8c11dd3f389450e948a70339b7dfb9c5a9d))
* store products priced in the tenant's currency ([#33](https://github.com/odvprogra/pricewright-api/issues/33)) ([710318c](https://github.com/odvprogra/pricewright-api/commit/710318ce13318b3e866384a6db8a3814010f5d11))


### Documentation

* close M2 in the README, CLAUDE.md, architecture and brief ([#37](https://github.com/odvprogra/pricewright-api/issues/37)) ([147cec9](https://github.com/odvprogra/pricewright-api/commit/147cec9df6b65f93fb0df91816a2a567b1797c28))

## [0.2.0](https://github.com/odvprogra/pricewright-api/compare/v0.1.0...v0.2.0) (2026-10-03)


### Features

* add a command to register a tenant and its admin ([#8](https://github.com/odvprogra/pricewright-api/issues/8)) ([9902931](https://github.com/odvprogra/pricewright-api/commit/99029319d00d005967c9e35760cbe90bcd134174))
* add login and current user endpoints ([#10](https://github.com/odvprogra/pricewright-api/issues/10)) ([8c93d22](https://github.com/odvprogra/pricewright-api/commit/8c93d220f687727c691c95c829da0c614874f093))
* add role-based permissions and the tenant endpoint ([#14](https://github.com/odvprogra/pricewright-api/issues/14)) ([fd3dfb6](https://github.com/odvprogra/pricewright-api/commit/fd3dfb66ebbb6df43e770250dbaffe599c041732))
* add users with tenant-scoped persistence ([#7](https://github.com/odvprogra/pricewright-api/issues/7)) ([bd180a7](https://github.com/odvprogra/pricewright-api/commit/bd180a7705405bd3635c08fc49f790ff41f81247))
* authenticate service accounts with API keys ([#21](https://github.com/odvprogra/pricewright-api/issues/21)) ([bbbaec2](https://github.com/odvprogra/pricewright-api/commit/bbbaec271ed2d181466fef39aba6ae0eca86ec73))
* authenticate users with passwords and access tokens ([#9](https://github.com/odvprogra/pricewright-api/issues/9)) ([e0f66af](https://github.com/odvprogra/pricewright-api/commit/e0f66af6bf0a0431103b8019002165fc92988ae8))
* let admins add users ([#17](https://github.com/odvprogra/pricewright-api/issues/17)) ([b29ffd5](https://github.com/odvprogra/pricewright-api/commit/b29ffd5fb09e0dd16652e12ad4f0b83a5382df61))
* let admins change the tenant with optimistic concurrency ([#15](https://github.com/odvprogra/pricewright-api/issues/15)) ([72215ae](https://github.com/odvprogra/pricewright-api/commit/72215ae81e6bf0218d0c155e9be2de443ff5a848))
* let admins edit and unlock users ([#18](https://github.com/odvprogra/pricewright-api/issues/18)) ([8d1c133](https://github.com/odvprogra/pricewright-api/commit/8d1c133651bf3e2e902dbe81dbbb05ecc3b0924e))
* let admins manage service accounts and API keys ([#20](https://github.com/odvprogra/pricewright-api/issues/20)) ([8eee144](https://github.com/odvprogra/pricewright-api/commit/8eee144fb0ffe6134097b60a63cab9b235ea975d))
* list and read the tenant's users ([#16](https://github.com/odvprogra/pricewright-api/issues/16)) ([4e03045](https://github.com/odvprogra/pricewright-api/commit/4e030455db9af8775f27e9bda5019acf89a39d63))
* model service accounts and API keys ([#19](https://github.com/odvprogra/pricewright-api/issues/19)) ([af8cca1](https://github.com/odvprogra/pricewright-api/commit/af8cca19c5869cf9affd915a77c22dbbfd79e1b9))
* persist tenants behind a unit of work ([#5](https://github.com/odvprogra/pricewright-api/issues/5)) ([ad6b8cd](https://github.com/odvprogra/pricewright-api/commit/ad6b8cdfb5ef6b30aa5f405d1f193b4692262dc9))
* rotate refresh tokens and detect reuse ([#13](https://github.com/odvprogra/pricewright-api/issues/13)) ([3954fb2](https://github.com/odvprogra/pricewright-api/commit/3954fb2e1e6974d6b292b4838ac4a24e3648da2d))
* store refresh token families ([#12](https://github.com/odvprogra/pricewright-api/issues/12)) ([2647c3d](https://github.com/odvprogra/pricewright-api/commit/2647c3d1020c85ccbbfd8f6d5aee29a38ff18dd3))


### Documentation

* close M1 in the README, CLAUDE.md and architecture ([#23](https://github.com/odvprogra/pricewright-api/issues/23)) ([c2d7df5](https://github.com/odvprogra/pricewright-api/commit/c2d7df51fa75ed5d5276764012d41d67bbbdca9d))

## 0.1.0 (2026-10-02)


### Documentation

* add product brief, README and ADR-0002 ([#1](https://github.com/odvprogra/pricewright-api/issues/1)) ([81d9589](https://github.com/odvprogra/pricewright-api/commit/81d958907b8eddaebaf5b1221ce0d3a94cbc0e81))


### Continuous Integration

* release with release-please and attach openapi.json ([#2](https://github.com/odvprogra/pricewright-api/issues/2)) ([31cc1b5](https://github.com/odvprogra/pricewright-api/commit/31cc1b5b3f67dfdc8b06665cd14b68e631e60ac3))
