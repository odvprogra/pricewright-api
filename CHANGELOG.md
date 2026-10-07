# Changelog

## [0.7.0](https://github.com/odvprogra/pricewright-api/compare/v0.6.0...v0.7.0) (2026-10-07)


### Features

* build Northfield's catalog from the demand dataset's top products ([#73](https://github.com/odvprogra/pricewright-api/issues/73)) ([61ca15c](https://github.com/odvprogra/pricewright-api/commit/61ca15c8df6b284e6c388b43a20808bd6825507a))
* seed a history of quotes and orders through the use cases ([#78](https://github.com/odvprogra/pricewright-api/issues/78)) ([b16446c](https://github.com/odvprogra/pricewright-api/commit/b16446c7f3f3b3d52dc9a9e152b20af28d300b90))
* seed the demo tenants' master data through the use cases ([#76](https://github.com/odvprogra/pricewright-api/issues/76)) ([2c32395](https://github.com/odvprogra/pricewright-api/commit/2c323951b4281b117f903a3246ee912de283a663))
* tell the demo tenants' quotes as stories of dated steps ([#77](https://github.com/odvprogra/pricewright-api/issues/77)) ([38045aa](https://github.com/odvprogra/pricewright-api/commit/38045aa75aedac8782ee6a8e8a2f8f11ce8843ae))


### Documentation

* close the presentable checkpoint in the README, CLAUDE.md, architecture and brief ([#79](https://github.com/odvprogra/pricewright-api/issues/79)) ([f0054c9](https://github.com/odvprogra/pricewright-api/commit/f0054c9646543c3b5f8298b5611b3095775ad804))

## [0.6.0](https://github.com/odvprogra/pricewright-api/compare/v0.5.0...v0.6.0) (2026-10-06)


### Features

* accept Idempotency-Key on every other creation ([#71](https://github.com/odvprogra/pricewright-api/issues/71)) ([f748633](https://github.com/odvprogra/pricewright-api/commit/f748633efb27b0ee9bdec5dd17afb58411a3ad4a))
* accept Idempotency-Key when starting a draft quote ([#64](https://github.com/odvprogra/pricewright-api/issues/64)) ([fc048e2](https://github.com/odvprogra/pricewright-api/commit/fc048e2509f39de6f62cdd147bfce589e430d6a9))
* cancel an open order with a reason ([#70](https://github.com/odvprogra/pricewright-api/issues/70)) ([58923a8](https://github.com/odvprogra/pricewright-api/commit/58923a83bb6fa4a2b1e0ea2c9300a0d04b152e37))
* convert accepted quotes into orders over the API ([#68](https://github.com/odvprogra/pricewright-api/issues/68)) ([29ea962](https://github.com/odvprogra/pricewright-api/commit/29ea96278ab631c779b19daa51edeaa014d7e8af))
* give tenants an order number prefix ([#65](https://github.com/odvprogra/pricewright-api/issues/65)) ([f154b54](https://github.com/odvprogra/pricewright-api/commit/f154b54b0b0e84f914eb0db2f9e10a1d57956f29))
* list orders with filters and keyset pages ([#69](https://github.com/odvprogra/pricewright-api/issues/69)) ([6f7b7c4](https://github.com/odvprogra/pricewright-api/commit/6f7b7c4dc78cbbf2923c208ff790bb0b1befa005))
* model orders converted once from accepted quotes ([#66](https://github.com/odvprogra/pricewright-api/issues/66)) ([8d812e1](https://github.com/odvprogra/pricewright-api/commit/8d812e1290f73a4aa0673f775caf88bae886d9f7))
* store idempotency keys per caller, held until the transaction ends ([#62](https://github.com/odvprogra/pricewright-api/issues/62)) ([cd7b1de](https://github.com/odvprogra/pricewright-api/commit/cd7b1deff1fb73e95644e164bfff34cea01b538c))
* store orders with their lines, numbered in a series of their own ([#67](https://github.com/odvprogra/pricewright-api/issues/67)) ([41dc09a](https://github.com/odvprogra/pricewright-api/commit/41dc09a80fed12895383eba65de43567cccb6bf2))


### Documentation

* close M6 in the README, CLAUDE.md, architecture and brief ([#72](https://github.com/odvprogra/pricewright-api/issues/72)) ([b2790fd](https://github.com/odvprogra/pricewright-api/commit/b2790fd526f17e83f579999800b59b864a65d4b3))

## [0.5.0](https://github.com/odvprogra/pricewright-api/compare/v0.4.0...v0.5.0) (2026-10-05)


### Features

* add, change and remove quote lines over the API, with manager overrides ([#57](https://github.com/odvprogra/pricewright-api/issues/57)) ([16eb45c](https://github.com/odvprogra/pricewright-api/commit/16eb45c8734417db23d7c8d3eeccd24b29591113))
* give tenants a quote number prefix and a default quote validity ([#49](https://github.com/odvprogra/pricewright-api/issues/49)) ([f13998b](https://github.com/odvprogra/pricewright-api/commit/f13998bacb1ada00469ce184b43de928c00066ce))
* let managers approve or reject quotes from an approval inbox ([#59](https://github.com/odvprogra/pricewright-api/issues/59)) ([83c1c29](https://github.com/odvprogra/pricewright-api/commit/83c1c2981c26586a4e6492edb2523072cfc990f3))
* let sales teams and integrations start draft quotes over the API ([#55](https://github.com/odvprogra/pricewright-api/issues/55)) ([5ed9b6e](https://github.com/odvprogra/pricewright-api/commit/5ed9b6e5815c28aca4895484e0c0b243a902b6f6))
* list quotes and change a draft's terms over the API ([#56](https://github.com/odvprogra/pricewright-api/issues/56)) ([9ffc453](https://github.com/odvprogra/pricewright-api/commit/9ffc453f4065c607fd205f7e877ed8ed030cc3af))
* model draft quotes priced by the engine, with a snapshot per line ([#52](https://github.com/odvprogra/pricewright-api/issues/52)) ([5bf6e86](https://github.com/odvprogra/pricewright-api/commit/5bf6e86ed48ffdb51fc6e1b9c50d7ac62ac0944f))
* model the quote lifecycle as a transition table ([#51](https://github.com/odvprogra/pricewright-api/issues/51)) ([cf6ab91](https://github.com/odvprogra/pricewright-api/commit/cf6ab9120171753ba7b05d4acc4785ea697b9eaf))
* move quotes through their lifecycle, with approvals decided by four eyes ([#53](https://github.com/odvprogra/pricewright-api/issues/53)) ([c271ac1](https://github.com/odvprogra/pricewright-api/commit/c271ac1a6a389163fce4a25d2c83117eca85a65a))
* store quotes with their lines, approvals and numbers ([#54](https://github.com/odvprogra/pricewright-api/issues/54)) ([56a3a2d](https://github.com/odvprogra/pricewright-api/commit/56a3a2d2ed0f36fe866c3144af2ef806e43f70fd))
* submit, recall, send, accept, cancel and revise quotes over the API ([#58](https://github.com/odvprogra/pricewright-api/issues/58)) ([eb55c05](https://github.com/odvprogra/pricewright-api/commit/eb55c052f4106baa4d9779dbd806fbe611a7e0d7))


### Documentation

* close M4 in the README, CLAUDE.md, architecture and brief ([#60](https://github.com/odvprogra/pricewright-api/issues/60)) ([de1a73f](https://github.com/odvprogra/pricewright-api/commit/de1a73f9e91e8903ce91ceee420d8a95dbefa57c))

## [0.4.0](https://github.com/odvprogra/pricewright-api/compare/v0.3.0...v0.4.0) (2026-10-04)


### ⚠ BREAKING CHANGES

* show unit costs only to people with costs:read ([#41](https://github.com/odvprogra/pricewright-api/issues/41))

### Features

* accept only ISO 4217 currencies, with their minor units ([#39](https://github.com/odvprogra/pricewright-api/issues/39)) ([2ca8cac](https://github.com/odvprogra/pricewright-api/commit/2ca8cac95ec21e044f764e7d4fc74de592d0e042))
* add the pricing engine with a price breakdown per line ([#43](https://github.com/odvprogra/pricewright-api/issues/43)) ([879d867](https://github.com/odvprogra/pricewright-api/commit/879d867731a2cb123a3392ff0d4cdf2533524662))
* let managers maintain pricing rules over the API ([#46](https://github.com/odvprogra/pricewright-api/issues/46)) ([aa7e635](https://github.com/odvprogra/pricewright-api/commit/aa7e635602f828a4080d5da10a8fa462fa6161a3))
* model pricing rules: scoped, effective-dated discounts and margin floors ([#42](https://github.com/odvprogra/pricewright-api/issues/42)) ([27d6d68](https://github.com/odvprogra/pricewright-api/commit/27d6d6867aa9e057658a67cbdec5c80c8eed2df1))
* preview prices with a step-by-step breakdown over the API ([#47](https://github.com/odvprogra/pricewright-api/issues/47)) ([3922c51](https://github.com/odvprogra/pricewright-api/commit/3922c51b70ed7c68b03cebd1128d6f17434d4aca))
* show unit costs only to people with costs:read ([#41](https://github.com/odvprogra/pricewright-api/issues/41)) ([41b0958](https://github.com/odvprogra/pricewright-api/commit/41b0958d700f43cb21e36d4a737e28a801574bb7))
* store pricing rules with their volume brackets ([#44](https://github.com/odvprogra/pricewright-api/issues/44)) ([a0c52ff](https://github.com/odvprogra/pricewright-api/commit/a0c52ff44690150b091d57c758cb03f29e278f32))


### Bug Fixes

* record decimals in the audit trail at their column's scale ([#45](https://github.com/odvprogra/pricewright-api/issues/45)) ([4263339](https://github.com/odvprogra/pricewright-api/commit/426333957c22d3d5b4ec5f6234a1788020762aba))


### Documentation

* close M3 in the README, CLAUDE.md, architecture and brief ([#48](https://github.com/odvprogra/pricewright-api/issues/48)) ([c5760ef](https://github.com/odvprogra/pricewright-api/commit/c5760ef5322ae9a860a7d6d1640f92786396f328))

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
