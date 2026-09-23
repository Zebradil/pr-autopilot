# Changelog

## [1.3.0](https://github.com/Zebradil/pr-autopilot/compare/v1.2.0...v1.3.0) (2026-09-23)


### Features

* add --version flag ([166e211](https://github.com/Zebradil/pr-autopilot/commit/166e211b2db34d15f8f34362dc5d18d6fc9cf796))
* colour, wrap and stream sweep output ([7500aad](https://github.com/Zebradil/pr-autopilot/commit/7500aada381e8286b0317aef8248188576be8426))
* gate by atlantis/plan check, set atlantis user name ([e2ea272](https://github.com/Zebradil/pr-autopilot/commit/e2ea272c0710ce76a8ede0ec84313dc39e02ebd7))
* onboard subcommand, --agent-command renamed to --agent ([6be8b42](https://github.com/Zebradil/pr-autopilot/commit/6be8b4233177bae2a26f80a1858ff913a4d31786))
* per-manager policies ([ae4cc3c](https://github.com/Zebradil/pr-autopilot/commit/ae4cc3cfa7d7da1894eb9219ed0d664bba46c5c4))
* split --config and --policy ([0f97c3c](https://github.com/Zebradil/pr-autopilot/commit/0f97c3c3f3ec7a506a95468c64eaeef3997ae3c3))


### Bug Fixes

* assess atlantis plan results when deciding ([a6c30ad](https://github.com/Zebradil/pr-autopilot/commit/a6c30ade12cffbe773b12c9ef7f30ebb143b2072))
* edit the state comment by numeric REST id, not GraphQL node id ([7500aad](https://github.com/Zebradil/pr-autopilot/commit/7500aada381e8286b0317aef8248188576be8426))
* escalate a PR whose repair could not run or did not fix it ([7500aad](https://github.com/Zebradil/pr-autopilot/commit/7500aada381e8286b0317aef8248188576be8426))
* keep the repair attempt count across writes within one sweep ([7500aad](https://github.com/Zebradil/pr-autopilot/commit/7500aada381e8286b0317aef8248188576be8426))
* skip Atlantis gate when plan touches no project ([556e7f9](https://github.com/Zebradil/pr-autopilot/commit/556e7f902a84045bdaf0a8e4bd7af6a11eca9d0c))

## [1.2.0](https://github.com/Zebradil/pr-autopilot/compare/v1.1.0...v1.2.0) (2026-09-21)


### Features

* add default preset ([5a7636b](https://github.com/Zebradil/pr-autopilot/commit/5a7636bf953c7abb1bd0fc1f26605aa4a47af50a))


### Bug Fixes

* **skill:** check for vacuous green ([59d214e](https://github.com/Zebradil/pr-autopilot/commit/59d214e9ec7f3b0f81d7d8be5a59f80d50a7149e))

## [1.1.0](https://github.com/Zebradil/pr-autopilot/compare/v1.0.2...v1.1.0) (2026-09-21)


### Features

* configuration presets ([bcdadbd](https://github.com/Zebradil/pr-autopilot/commit/bcdadbd034598057856159e74f5f3b35acfc15af))

## [1.0.2](https://github.com/Zebradil/pr-autopilot/compare/v1.0.1...v1.0.2) (2026-09-21)


### Bug Fixes

* classify Renovate pinDigest updates as pin ([#12](https://github.com/Zebradil/pr-autopilot/issues/12)) ([2b4fc10](https://github.com/Zebradil/pr-autopilot/commit/2b4fc107ea6ea85c436fcd2d6c06da676e25630f))

## [1.0.1](https://github.com/Zebradil/pr-autopilot/compare/v1.0.0...v1.0.1) (2026-09-21)


### Bug Fixes

* update create-github-app-token action ([2820d17](https://github.com/Zebradil/pr-autopilot/commit/2820d17a1825e3952aa42f5b31cdeb472a7301cb))

## 1.0.0 (2026-09-21)


### Features

* pr-autopilot v1 — deterministic bot PR triage ([200e7ee](https://github.com/Zebradil/pr-autopilot/commit/200e7ee78e426292669da0f533f00bd2b0f3fadd))
* ship runtime as a composite action ([#4](https://github.com/Zebradil/pr-autopilot/issues/4)) ([a7408c7](https://github.com/Zebradil/pr-autopilot/commit/a7408c729d1974d1a8a30af7d7ed028d00d358d7))
* support GitHub App identity in workflows ([5a87b6f](https://github.com/Zebradil/pr-autopilot/commit/5a87b6f6b48b6fd0756134e18b6ad93fecbddf87))


### Bug Fixes

* emit the upgrades marker from prHeader ([d96fd0d](https://github.com/Zebradil/pr-autopilot/commit/d96fd0d8fe18e01e029080082be64b4bb5bfc34a))
* fail the sweep when gh cannot reach GitHub ([b7888ab](https://github.com/Zebradil/pr-autopilot/commit/b7888ab0fe2016e2766e7f74d12301a00fdda7c2))
* let the reactive template report a real failure ([a8ff57b](https://github.com/Zebradil/pr-autopilot/commit/a8ff57b95913259e798d38e46380d1775e9c3cdc))
* stop reporting a healthy sweep as a failure ([bc62962](https://github.com/Zebradil/pr-autopilot/commit/bc629623682e00ed0bd7810f5a9d01770c004885))
