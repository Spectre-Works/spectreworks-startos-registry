# Upstream release monitor

Deployed on `bitcoin.local` on 2026-10-14. Source lives in this management
repository; production code lives at `/opt/startos-upstream-monitor/monitor`.
The registry daemon and other Bitcoin services are not restarted by this monitor.

## Runtime and ownership

- Persistent unit: `startos-upstream-monitor.service`, enabled at boot, running
  as the unprivileged `startos-monitor` system account.
- Scan immediately on start, then six hours after each successful scan.
  Failed source checks retry after 15 minutes. Undelivered notifications retry
  every minute while waiting. No package code, build, or installation is executed.
- Independent watchdog: `startos-upstream-monitor-health.timer`, every 15
  minutes. Alerts if the service is inactive or a full successful scan is older
  than seven hours; allows 30 minutes for the initial scan. `OnFailure` also
  invokes it when the monitor process exits unsuccessfully. The watchdog is a
  separate script and does not import monitor code, so it can report a broken
  monitor deployment.
- Policy: `config/upstream-monitor.json` →
  `/etc/startos-upstream-monitor/policy.json` (root:root, 0644).
- State: `/var/lib/startos-upstream-monitor/state.sqlite3`, owned by
  `startos-monitor`, directory mode 0700. SQLite stores HTTP ETags, scan
  timestamps, catalog snapshots, promotion audit records, and the ntfy outbox.
- Protected notification environment:
  `/etc/startos-upstream-monitor/notifications.env` (root:root, 0600).
  `NTFY_URL` is required; `NTFY_TOKEN` and `GITHUB_TOKEN` are optional.
  The actual topic and tokens are not committed to this repository.
  `notifications.env.example` is a blank template. Fill it on the deployment
  host and keep the resulting file root-owned with mode 0600. Environment files
  and runtime SQLite databases are ignored by Git.
- Signing key: `/etc/startos-upstream-monitor/signing-key.pem` (root:root,
  0600), exposed only to the monitor through systemd `LoadCredential`.
- Signer ID: `MJXI3RCSCVBTKD2IICROOPRBWYBVSFM3`,
  **SPECTRE WORKS Automated Package Monitor**. Authorized for `bitcoind`
  (`#knots`), `fulcrum` and `canary` (`#blake`), `datum` (`#pow`), and `mempool`
  (`#mpg`) only. This signer is not a registry administrator. The original
  administrator key remains on the workstation.
- No listener or new firewall port. Outbound HTTPS reaches the public
  destination registry, approved source registries, GitHub, and ntfy.
- systemd confines filesystem writes to the state directory, removes
  capabilities, and caps CPU at 25% of one core and tasks at 64. The unit requests
  a 384 MiB memory cap, but this node's cgroup controllers are `cpuset cpu io pids`:
  the memory controller is absent, so `MemoryMax` is not currently enforced.
  Observed idle monitor RSS after a completed scan was about 60 MiB.

CLI pin: `start-cli 2.3.0`, ARM64 official release package:

https://github.com/Start9Labs/start-technologies/releases/download/start-cli/v2.3.0/start-cli_2.3.0_arm64.deb

SHA-256:
`f0299fccc3e73edd43ee05bac7308ac7216920f5bbb6ce7f9023e85015c6882e`.
The binary is extracted, without replacing system packages, to
`/opt/startos-upstream-monitor/bin/start-cli`. Node uses the node's existing
v20.19.5 runtime. ExVer and its two dependencies are pinned in `vendor/`;
`vendor/SHA256SUMS` records their installed source hashes. No npm install or
compilation is performed on the node.

## Active policy: follow the upstream registry

As requested by the operator on 2026-11-05, `trackingSource` is
`https://start9.mempool.guide`. For each registered/configured package, copy the
newest higher version of its configured flavor from that registry. The source
registry decides release readiness: StartOS requirements, dependency ranges
(including null/legacy ranges), migration eligibility, hardware/capability
changes, prerelease status, signing-key rotation, packaging/asset repository
moves, and operator warnings do not require a separate local approval.
This is delegated upstream release policy, not independent compatibility
certification. StartOS still evaluates packages for the installing device.

Only the five configured app/flavor pairs are tracked. No automatic new-app
registration, flavor switching, downgrade, deletion, or replacement of an
already-published version occurs. Prior versions and categories are retained.
GitHub-only assets are not published ahead of the source registry. Application
release discovery and packaging-needed alerts are disabled in tracking mode;
only the source registry's published catalog drives updates. Checks still run
every six hours, with 15-minute retries on failures.

The source must be an approved HTTPS registry. The monitor requires complete
artifact metadata and GitHub Release `.s9pk` URLs. Every artifact is downloaded
temporarily (maximum 2 GiB) and verified against GitHub's SHA-256 digest. Native
CLI inspection must match the source package ID, version, commitment, upstream
and packaging identities, StartOS requirement, dependency declarations, and
migration declaration. Source catalog metadata and the destination snapshot
are rechecked immediately before signing; changes abort and retry. The exact
inspected bytes are added with their original URLs. No package code runs.

The configured HTTPS registry and its published commitments are the release
trust authority in this mode; the legacy per-package signer/repository pins
are not additional approval gates. The native CLI, scoped service signing key,
download bounds, publication journal, destination commitment verification, and
ntfy success receipts remain in use. The catalog records the artifact's own
metadata; missing dependency ranges are never rewritten to `*`.

Publication is journaled as pending before submission and verified afterward
against destination hardware requirements, URLs, and commitments. On restart,
completed publications are recognized and their success notices recovered.
An absent publication retries preflight. Conflicting or partial publications
stop updates for operator recovery. Multi-artifact publication is not a registry
transaction. Cross-package releases may be copied in separate steps, as they
appear upstream; independent compatibility planning is not performed.
Registry publication changes marketplace availability, not the host's services
or customers' installed software. The scoped service key remains non-admin.

Without `trackingSource`, the legacy curated mode remains available in code:
it applies explicit repository/signer/OS pins and local dependency, migration,
hardware, capability, and warning gates, plus approved GitHub-only discovery.
Those gates caused the historical holds and one-time overrides below. Removing
`trackingSource` is a deliberate return to that stricter mode, not a requirement
for ordinary tracking updates.

## Notifications and recovery

Alerts contain the app, upstream or package version, reason, and a release link.
Unchanged packaging/hold events are deduplicated durably. Source failures notify
after more than three consecutive failed checks (on the fourth failure), for
either an individual app's upstream check or a failure of the entire scan.
Successful checks reset the relevant counter. The same failure stays quiet
within an incident, but a new incident after recovery can alert again.
Failed checks retry every 15 minutes. Verified package updates emit a
`StartOS: package updated` notice with the previous and new versions. This notice
is queued in the same SQLite transaction that marks publication verified, so an
interruption cannot lose the success event. Restart reconciliation queues the
same deduplicated event after confirming an interrupted publication completed.
No success notice is emitted for an attempted or failed publication.
Action-required alerts use priority 4; successful updates use priority 3.
An outbox lease prevents the service and watchdog sending the same queued event
concurrently. Failed delivery remains pending. The publisher checks ntfy's
acknowledged event type and exact topic, and stores the provider message ID,
topic, and time in the event's `receipt` column. The historical `delivered`
column means ntfy accepted publication; it does not prove push delivery to a
device. Check the subscription's exact topic when investigating a missing notice.
A delivery accepted by ntfy but
whose response is lost can still be retried; exactly-once HTTP delivery is not
guaranteed.

Commands on `bitcoin.local`:

```bash
systemctl status startos-upstream-monitor.service
sudo journalctl -u startos-upstream-monitor.service -n 50 --no-pager
systemctl list-timers startos-upstream-monitor-health.timer
sudo systemctl restart startos-upstream-monitor.service  # immediate scan
sudo systemctl start startos-upstream-monitor-health.service
```

Inspect status without exposing secrets:

```bash
sudo -u startos-monitor python3 - <<'PY'
import json, sqlite3
db = sqlite3.connect('/var/lib/startos-upstream-monitor/state.sqlite3')
for key in ('lastSuccess', 'lastResult', 'nextCheck', 'lastPromotion'):
    row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
    print(key, json.loads(row[0]) if row else None)
print('pending alerts', db.execute(
    'SELECT count(*) FROM events WHERE delivered IS NULL').fetchone()[0])
PY
```

To pause: stop the service and the watchdog timer together so the intentional
pause does not produce stale-monitor alerts. To resume: start both. To revoke
publishing immediately, use the workstation administrator:

```bash
start-cli --registry https://startos-registry.spectreworks.io registry admin signer remove MJXI3RCSCVBTKD2IICROOPRBWYBVSFM3
```

If the node is lost, recreate its service key and register a new package-scoped
signer using the workstation administrator; update `serviceSignerId`. Never copy
the workstation administrator key to the node. The existing registry data backup
does not include monitor state, notification credentials, or this private key.
Back up the SQLite state with Python's `sqlite3.Connection.backup()` and protect
any backup containing `/etc/startos-upstream-monitor` as a signing credential.

## Validation and updating the deployment

Before transferring changes:

```bash
python3 -m py_compile monitor/monitor.py
python3 -m unittest discover -s monitor -p 'test_*.py'
(cd monitor/vendor && sha256sum -c SHA256SUMS)
```

Copy `monitor/` to `/opt/startos-upstream-monitor/monitor`, policy to
`/etc/startos-upstream-monitor/policy.json`, and units from `deploy/` to
`/etc/systemd/system`. Keep code root-owned. Preserve the private key and
notification environment. Run `systemd-analyze verify`, rerun the tests on ARM64
as `startos-monitor` with `python3 -B`, then daemon-reload and restart the monitor.
Confirm a completed cycle, no pending notification delivery failures, and the
watchdog. Mirror the deployed public policy and unit files in
`bitcoin_local/configs/`; update `bitcoin_local/AGENTS.md` and its registry runbook.

Read-only discovery can be run with a separate state directory:

```bash
python3 monitor/monitor.py --config config/upstream-monitor.json \
  --state-dir /tmp/startos-monitor-preview --once --dry-run
```

That config uses the node's CLI path. On a workstation, make a temporary copy
with its `cli` field set to the installed local `start-cli`. Dry-run sends no
alerts and performs no registration; in tracking mode it reports newer source-registry versions that would be copied.
In legacy curated mode it also reports GitHub-only assets requiring inspection. Temporary artifacts from interrupted
production downloads are removed on startup while holding the process lock.

Rollout validation: 34 focused tests passed on the workstation and ARM64 node;
the native ARM64 CLI inspected and matched DATUM `#pow:0.5.0:1` against source
metadata without publishing it. Production completed a full scan and delivered
two hold alerts; unchanged alerts were suppressed on subsequent scans.

## Operator override on 2026-10-29

The operator explicitly requested bypassing the held compatibility requirements
and publishing Knots `#knots:29.4.3:1`, Canary `#blake:1.7.0:1`, and DATUM
`#pow:0.5.0:3`. Publication uses the workstation administrator, retains old
versions and upstream manifests/URLs, and verifies GitHub asset digests, exact
source commitments, identities, and migration declarations. This is a one-time
publication override, not runtime compatibility certification or a change to the
automatic monitor policy. Knots declares StartOS `0.4.0.2`, moves its asset to
`Horizon-btc/knots-startos`, and narrows its declared hardware architectures.
Canary and DATUM still lack explicit dependency version ranges. Publication
records and exact commitments are recorded in `state/catalog-lock.json`.

## Operator override on 2026-11-02

The operator requested the latest packages and explicitly instructed ignoring
conflicts. A fresh scan of all five registered apps completed without source
failures. Mempool `#mpg:3.4:10` (`v3.4-beta7`) was the only newer available
package and was published with the workstation administrator. This one-time
override bypassed the changed asset repository (`Horizon-btc/mempool-startos`)
and unknown dependency ranges in the affected dependency component. GitHub's
SHA-256 digest, exact source commitment, package identity, manifest dependency
declarations and migration declaration were verified before publication. The
live destination commitment was verified afterward; all previous versions and
category assignments were retained. The publication and digest are recorded in
`state/catalog-lock.json`. Automatic monitor policy remains unchanged; this is
not runtime compatibility certification.

Fulcrum upstream `v2.1.4-blake` had no matching StartOS package in the checked
sources, so `#blake:2.1.3:1` remains the latest published package. Knots, Canary
and DATUM already matched their latest available package versions.

## Catalog reader recovery on 2026-11-05

The upstream registry began returning content-addressed icon paths (for example,
`blake3/<hash>.png`). The pinned CLI 2.3.0 expects base64 data URLs and rejected
the complete catalog with a deserialization error, preventing scans and triggering
the seven-hour watchdog. Public catalog reads now use the registry's unsigned
`POST /rpc/v0` method `package.index` directly, with a 180-second timeout, a
32 MiB response limit, and response/package-map validation. Metadata, including
icons, commitments, dependency constraints, and download URLs, is preserved.
The pinned native CLI still inspects artifacts and signs publications; source
trust and compatibility gates are unchanged. All 41 tests passed locally and on
the ARM64 node before restart.

## Tracking rollout on 2026-11-05

The operator authorized following packages published by the upstream registry
without repeated manual compatibility overrides. `trackingSource` enables this
policy for the existing five flavors. All 48 tests passed locally and on ARM64,
including changed OS/legacy dependency metadata, no downgrade/flavor switch,
no GitHub-only discovery, dry-run behavior, and source changes during preflight.

The modern registry tags Merkle commitments with `type: merkle-archive`; the
pinned inspector returns the same hash/size without that label. Comparison
normalizes only that known label, preserving hash/size checks and rejecting
unknown commitment types.

Production completed at 09:38 MST with all five latest source versions matched,
zero source failures, no pending notifications, and a clear watchdog. The scoped
monitor automatically published Canary `#blake:1.7.1:0` and Fulcrum
`#blake:2.1.4:0`; their exact commitments are recorded in `state/catalog-lock.json`
and the node's verified promotion audit. All recorded prior versions and the
five category assignments remain present. Next scheduled scan: 15:38 MST.
