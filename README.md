# Spectre Works StartOS registry

Management repository for the Spectre Works XBT package registry. Live registry data is not stored here.

## Where things run

| Role | Host |
| --- | --- |
| `start-cli`, admin private key, this Git repository | Gentoo workstation |
| Registry container, registry data, Cloudflare Tunnel | `bitcoin.local` |

The admin private key stays at `~/.startos/id.key.pem` on the workstation. It is not on `bitcoin.local`.

Public URL, once the edge certificate covers this name:

`https://registry.startos.spectreworks.io`

The registry daemon listens only on `127.0.0.1:5959` inside Docker on `bitcoin.local`. Cloudflare Tunnel is the public ingress. There is no extra reverse proxy and no router port forward.

## Production pin

Image digest of upstream `ghcr.io/start9labs/startos-registry:v1.1.1`, `linux/arm64`:

`ghcr.io/start9labs/startos-registry@sha256:5eb441583842b80792b81048eb2658da932b8452bd26a85b6d41bed883373768`

Live files on `bitcoin.local`:

- Compose: `/srv/startos-registry/compose.yaml`
- Data: `/srv/startos-registry/data`
- Rolling backup: `/var/lib/bitcoinknots/startos-registry-backup.tar.zst`
- Backup timer: `startos-registry-backup.timer` (daily, 03:17 America/Denver, plus up to 10 minutes)
- Health timer: `startos-registry-health.timer` (about every 5 minutes)

Run a backup now:

```bash
sudo systemctl start startos-registry-backup.service
```

The backup stops only the `startos-registry` container while it copies `/srv/startos-registry/data`, then starts that container again before compressing. Docker, the tunnel, and Bitcoin Knots stay up.

## Cloudflare

The existing remotely managed tunnel `spectreworks-btcpay` (`1915bd31-93b1-4eec-9eb1-246cc22cffe6`) also serves this hostname. `pay.spectreworks.io` still goes to `http://127.0.0.1:23000`. The pre-change ingress is saved on the Pi at `/etc/cloudflared/spectreworks-btcpay.ingress.pre-startos-registry.json`.

DNS: proxied CNAME `registry.startos.spectreworks.io` to `1915bd31-93b1-4eec-9eb1-246cc22cffe6.cfargotunnel.com`.

Universal SSL on the free `spectreworks.io` zone covers `*.spectreworks.io` only. `registry.startos.spectreworks.io` is a second-level name, so the edge currently answers the TLS handshake with a failure. The available API tokens cannot enable Total TLS. Enable it before clients can connect:

Cloudflare dashboard → `spectreworks.io` → SSL/TLS → Edge Certificates → Total TLS → On.

## Administrator

Signer ID `HDV3ZGRM5I4QNWBUGY3ZCODIACDJOHSO`, name Spectre Works Release Administrator, contact `mailto:contact@spectreworks.io`.

## Catalog

`state/source-index.json` is the package index from `https://start9.mempool.guide`. `state/promotion-plan.json` is the proposed import. `state/catalog-lock.json` is empty until a package is promoted.

See `config/catalog-policy.md`. Do not promote a package until it is approved. Promotion endorses that package commitment with the Spectre Works key.

```bash
scripts/promote-package.sh <package-id> <version-key>
```

This registry does not publish StartOS operating-system updates.
