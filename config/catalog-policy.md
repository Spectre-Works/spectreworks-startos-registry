# Catalog policy

SPECTRE WORKS signs a package commitment when that package is promoted into `https://startos-registry.spectreworks.io`. Promotion keeps the upstream asset URL. The monitor downloads it temporarily for verification and retains the upstream URL; it does not rehost the `.s9pk`.

`state/promotion-plan.json` is the inspection of `https://start9.mempool.guide` on 2026-10-07. The approved import is the newest revision of each package only.

Categories:

| ID | Name | Package |
| --- | --- | --- |
| `xbt-nodes` | XBT Nodes | `bitcoind` |
| `indexers` | Indexers | `fulcrum` |
| `mining` | Mining | `datum` |
| `explorers` | Explorers | `mempool` |
| `wallets` | Wallets | `canary` |

Category IDs stay stable after they are published.

This registry is not a StartOS operating-system update source. Do not publish OS images or change a device's `ui.startosRegistry`.

`bitcoind` in the source registry is the Bitcoin Knots flavor (`#knots:`). Promoting it records marketplace metadata. It does not change Bitcoin Knots on `bitcoin.local`.

## Automatic upstream tracking

Operator policy as of 2026-11-05: track `https://start9.mempool.guide` through
`trackingSource` in `config/upstream-monitor.json`. Publish its newest higher
version of each existing configured flavor after artifact/digest/commitment
verification. Upstream owns compatibility and release policy; local OS,
dependency, hardware, repository, signer and prerelease pins do not hold an
indexed release. Do not publish GitHub-only releases ahead of that registry.
Keep prior versions/categories, the scoped signer, audit journal, and final
publication verification. See `monitor/README.md` for exact trust boundaries.
