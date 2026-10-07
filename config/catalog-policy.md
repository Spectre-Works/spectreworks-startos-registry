# Catalog policy

Spectre Works signs a package commitment when that package is promoted into `https://registry.startos.spectreworks.io`. Promotion keeps the upstream asset URL. It does not download or rehost the `.s9pk`.

Nothing has been promoted yet. `state/promotion-plan.json` is the inspection of `https://start9.mempool.guide` on 2026-10-07. The proposal is the newest revision of each package only.

Proposed categories, to be created after approval:

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
