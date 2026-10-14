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

## Source and release policy

Every run reads the live destination catalog. Newly registered apps need an
explicit source mapping and signer authorization; the monitor alerts when a
mapping is missing. Removed apps cease being scanned. Adding another flavor
requires a deliberate policy/signer-scope change.

Both application release repositories and approved packaging repositories are
checked through GitHub's paginated release API, with cached ETags. Package
revisions in the source registry are also checked, even without a new application
release. The default page bound is 2,000 releases per repository; exhaustion
holds the check and alerts after repeated failure rather than silently truncating.

The policy pins application forks, packaging/asset repositories, current
flavors, known upstream signing keys, and the existing StartOS requirement.
Application repositories may themselves provide `.s9pk` assets. GitHub-only
assets are explicitly enabled in each app's policy; their trust boundary is
the approved repository and GitHub's SHA-256 asset digest. Indexed candidates
additionally require a known signing-key entry and an exact package commitment.
Unknown signers, identities, asset hosts, or StartOS requirements are held.

DATUM and Mempool retain prerelease lanes because the existing XBT catalog
already uses beta packages. Knots, Fulcrum, and Canary ignore releases marked
prerelease or named alpha/beta/RC. Policies are explicit rather than relying on
GitHub's `latest` endpoint. DATUM's `v0.5.0beta` tag maps to the upstream numeric
version `0.5.0` used by its wrapper. Knots build/date tag changes are tracked
even when the numeric version does not increase. Unsupported new tag formats
fail the source check and alert; they are not guessed.

`baselinePublishedAt` is the original approved catalog date. Releases with the
same upstream number are matched using tag evidence in package asset URLs.
Release identity is not inferred solely from the existence of an old `.s9pk`.
A new application release without a matching approved package emits a structured
`packaging_required` event and ntfy alert. There is no packaging agent or
automatic build/hosting process in this phase.

## Compatibility and publication

Start9's ExVer implementation evaluates flavors, revisions, migration ranges,
and dependency constraints. The gate checks the updated app, reverse consumers,
and their transitive dependencies. Required dependencies must be present and
satisfy their declared range (including declared `satisfies` aliases). Optional
dependencies are checked when their provider is in the registry. Required
cycles, hardware regressions, changed runtime capabilities, and pre-download
operator warnings hold publication.

**A missing/null dependency `versionRange` is unknown, not unrestricted.** Legacy
packages can enforce dependency compatibility in runtime JavaScript that the
catalog does not expose. The monitor never runs that JavaScript. An upstream
wrapper must publish explicit constraints before automatic promotion can be
established. Do not replace null constraints with `*` just to bypass the gate.

On deployment, DATUM candidates `#pow:0.4.1:27` and `#pow:0.5.0:1` were held
because `datum -> bitcoind` has no published version range. This means
compatibility is unverified; it is not evidence of a conflicting constraint.
No application package was promoted during rollout. The notification destination
was corrected on 2026-10-14 after a one-character topic mismatch was identified;
both hold notices were republished and the operator confirmed visibility.

Later on 2026-10-14, the operator explicitly verified compatibility and requested
publication of DATUM `#pow:0.5.0:1`. That exact version was published after the
normal source, artifact, commitment, hardware, and migration checks; the prior
`#pow:0.4.1:26` remains available. The version-specific operator approval,
candidate/catalog fingerprints, and commitments are recorded in the node's
SQLite key `compatibilityApproval:datum:#pow:0.5.0:1` and the promotion audit;
`state/catalog-lock.json` records the publication. The success notice was
accepted by ntfy on the corrected topic. No dependency ranges were rewritten,
and future releases remain subject to the normal gate.

For an eligible candidate, all assets are downloaded temporarily (maximum 2 GiB
per asset), verified against GitHub's digest, and inspected by the native CLI.
Identity, version, source commitment, migration declaration, and dependencies
must match indexed metadata. The destination snapshot is rechecked before
mutation. The native `registry package add --url ...` path signs the exact
inspected artifact and verifies remote bytes before submission; this avoids
`promote` re-reading mutable source metadata after preflight. The original
upstream URL is retained; the node does not host package downloads. Old versions
and category assignments remain in the destination catalog.

Publication is journaled as pending before submission and verified afterward
against destination requirements, URLs, and commitments. On restart, a complete
publication is recognized; an absent publication is queued for another preflight.
Conflicting or partial publication stops updates and alerts for operator recovery.
Multi-artifact publication is not a registry transaction. Cross-package changes
are applied only when each intermediate catalog is compatible; changes that
require an incompatible simultaneous transition are held.

These are catalog compatibility checks, not runtime integration tests or a
guarantee for every customer's installed version/configuration. Registry updates
do not upgrade the software on `bitcoin.local` or customer nodes.

## Notifications and recovery

Alerts contain the app, upstream or package version, reason, and a release link.
Unchanged packaging/hold events are deduplicated durably. Source failures notify
after two consecutive failed checks. Verified package updates emit a
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
alerts and performs no registration; it reports eligible indexed candidates
and GitHub-only assets requiring inspection. Temporary artifacts from interrupted
production downloads are removed on startup while holding the process lock.

Rollout validation: 34 focused tests passed on the workstation and ARM64 node;
the native ARM64 CLI inspected and matched DATUM `#pow:0.5.0:1` against source
metadata without publishing it. Production completed a full scan and delivered
two hold alerts; unchanged alerts were suppressed on subsequent scans.
