# SPECTRE WORKS StartOS registry

Management repository for the SPECTRE WORKS XBT package registry. Live registry data is not stored here.

## Where things run

| Role | Host |
| --- | --- |
| `start-cli`, admin private key, this Git repository | Gentoo workstation |
| Registry container, registry data, Cloudflare Tunnel | `bitcoin.local` |

The admin private key stays at `~/.startos/id.key.pem` on the workstation. It is not on `bitcoin.local`.

Public URL:

`https://startos-registry.spectreworks.io`

Description: `Curated StartOS packages for the Bitcoin (XBT) network, maintained by SPECTRE WORKS.`

The registry icon is `deploy/registry-icon.png`, made from the SPECTRE WORKS emblem in the bitcoinblake2bmining.com brand assets.

The registry daemon listens only on `127.0.0.1:5959` inside Docker on `bitcoin.local`. Cloudflare Tunnel is the public ingress. There is no extra reverse proxy and no router port forward.

## Production pin

Image digest of upstream `ghcr.io/start9labs/startos-registry:v1.1.1`, `linux/arm64`:

`ghcr.io/start9labs/startos-registry@sha256:5eb441583842b80792b81048eb2658da932b8452bd26a85b6d41bed883373768`

Live files on `bitcoin.local`:

- Compose: `/srv/startos-registry/compose.yaml`
- Data: `/srv/startos-registry/data`
- Container resolver: `/srv/startos-registry/resolv.conf`
- DNS stub: `/usr/local/sbin/startos-registry-dns` (`startos-registry-dns.service`)
- Rolling backup: `/var/lib/bitcoinknots/startos-registry-backup.tar.zst`
- Backup timer: `startos-registry-backup.timer` (daily, 03:17 America/Denver, plus up to 10 minutes)
- Health timer: `startos-registry-health.timer` (about every 5 minutes)

Run a backup now:

```bash
sudo systemctl start startos-registry-backup.service
```

The backup stops only the `startos-registry` container while it copies `/srv/startos-registry/data`, then starts that container again before compressing. Docker, the tunnel, and Bitcoin Knots stay up.

## Container DNS

Promotion downloads each `.s9pk` on `bitcoin.local` long enough to check the signed commitment. The file is not kept, and the published URL stays on GitHub. Docker 20.10 on this Pi puts `127.0.0.11` in a user-defined network, and that embedded resolver cannot use the host's Tailscale DNS. The host firewall also drops forwarded traffic unless a rule allows it.

`startos-registry-dns.service` listens on the compose gateway `172.18.0.1:53`. It answers A queries from the host resolver. It answers AAAA with no addresses, because this Pi has no working IPv6 path and an AAAA lookup would stall. Compose bind-mounts `/srv/startos-registry/resolv.conf` over the container's `/etc/resolv.conf`.

`/etc/nftables.conf` allows that DNS query to the gateway, and it allows the `172.18.0.0/16` compose network to open outbound connections through `eth0`. Docker is not managing iptables here. Those rules do not publish the registry. It still listens only on `127.0.0.1:5959`.

The compose network is pinned to `172.18.0.0/16`. The stub and the firewall rules use that gateway. If the network is recreated on a different subnet, update the stub, `resolv.conf`, and the nftables rules together.

## Cloudflare

The existing remotely managed tunnel `spectreworks-btcpay` (`1915bd31-93b1-4eec-9eb1-246cc22cffe6`) also serves this hostname. `pay.spectreworks.io` still goes to `http://127.0.0.1:23000`. The pre-change ingress is saved on the Pi at `/etc/cloudflared/spectreworks-btcpay.ingress.pre-startos-registry.json`.

DNS: proxied CNAME `startos-registry.spectreworks.io` to `1915bd31-93b1-4eec-9eb1-246cc22cffe6.cfargotunnel.com`.

Universal SSL on the free `spectreworks.io` zone covers `*.spectreworks.io`. This hostname is a single label under the zone, so the edge certificate matches it.

## Administrator

Signer ID `HDV3ZGRM5I4QNWBUGY3ZCODIACDJOHSO`, name SPECTRE WORKS Release Administrator, contact `mailto:contact@spectreworks.io`.

## Catalog

`state/source-index.json` is the package index from `https://start9.mempool.guide`. `state/promotion-plan.json` is the approved import. `state/catalog-lock.json` records each promoted version.

See `config/catalog-policy.md`. Promotion endorses that package commitment with the SPECTRE WORKS key.

```bash
scripts/promote-package.sh <package-id> <version-key>
```

Quote version keys. They contain `#`.

This registry does not publish StartOS operating-system updates.

## Upstream monitoring

The persistent release monitor on `bitcoin.local` checks registered apps every
six hours, publishes verified compatible packages, and sends notifications for
packaging needs, held updates, failures, and successful updates. See
[the monitor runbook](monitor/README.md).

Set `NTFY_URL` in the node's protected notification environment file. The public
repository includes only a [blank template](monitor/notifications.env.example);
the real destination and credentials remain on the deployment host.

## Hookup

On a StartOS node: Marketplace → Add custom registry → `https://startos-registry.spectreworks.io`.

Package files download from the GitHub Release URLs stored in the index. The registry host does not serve the `.s9pk` files.
