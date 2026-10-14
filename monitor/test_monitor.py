"""Safety and recovery tests; no network, signing, or production writes."""
import copy
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("monitor", Path(__file__).with_name("monitor.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
hspec = importlib.util.spec_from_file_location("healthcheck", Path(__file__).with_name("healthcheck.py"))
h = importlib.util.module_from_spec(hspec)
hspec.loader.exec_module(h)


def metadata(deps=None):
    return {"dependencyMetadata": deps or {}, "sourceVersion": "*", "s9pks": [], "satisfies": []}


def dependency(version_range="*", optional=False):
    return {"optional": optional, "versionRange": version_range}


def catalog(entries):
    return {"packages": {p: {"versions": {v: meta}} for p, (v, meta) in entries.items()}}


class VersionTests(unittest.TestCase):
    def test_native_flavor_and_revision_semantics(self):
        self.assertTrue(m.matches("#blake:2.1.3:2", ">#blake:2.1.3:1"))
        self.assertFalse(m.matches("#other:2.1.3:2", ">#blake:2.1.3:1"))

    def test_migration_expression(self):
        self.assertTrue(m.matches("#blake:2.1.3:1", "<=#blake:2.1.3:1 || (>=2:0 <3:0)"))
        self.assertFalse(m.matches("#knots:29.4.2:4", "<=#blake:2.1.3:1"))

    def test_numeric_sort_and_prerelease(self):
        self.assertEqual(m.latest(["#x:1:9", "#x:1:10", "#y:3:0"], "x"), "#x:1:10")
        self.assertEqual(m.exver("upstreamCompare", a="1.0-rc.1", b="1.0"), -1)

    def test_malformed_constraint_holds(self):
        with self.assertRaises(m.Hold):
            m.matches("#x:1:0", "not a range")


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.entries = {"node": ("#knots:29:0", metadata()),
                        "indexer": ("#blake:2:0", metadata({"node": dependency(">=#knots:29:0 <#knots:30:0")})),
                        "wallet": ("#blake:1:0", metadata({"indexer": dependency(">=#blake:2:0 <#blake:3:0")}))}
        self.catalog = catalog(self.entries)
        self.selected = {p: v for p, (v, _) in self.entries.items()}

    def guard(self, p, v, meta):
        m.dependency_guard(self.catalog, self.selected, p, v, meta)

    def test_compatible_forward_chain(self):
        self.guard("wallet", "#blake:1:1", self.entries["wallet"][1])

    def test_reverse_dependency_conflict(self):
        with self.assertRaisesRegex(m.Hold, "indexer needs node"):
            self.guard("node", "#knots:30:0", metadata())

    def test_transitive_dependency_conflict(self):
        self.catalog["packages"]["indexer"]["versions"]["#blake:2:0"]["dependencyMetadata"]["node"]["versionRange"] = ">=#knots:30:0"
        with self.assertRaisesRegex(m.Hold, "indexer needs node"):
            self.guard("wallet", "#blake:1:1", self.entries["wallet"][1])

    def test_unknown_legacy_range_holds(self):
        candidate = metadata({"node": dependency(None)})
        with self.assertRaisesRegex(m.Hold, "range unknown"):
            self.guard("indexer", "#blake:2:1", candidate)

    def test_optional_absent_and_present(self):
        self.guard("wallet", "#blake:1:1", metadata({"tor": dependency(None, True)}))
        with self.assertRaisesRegex(m.Hold, "range unknown"):
            self.guard("wallet", "#blake:1:1", metadata({"node": dependency(None, True)}))

    def test_missing_required_dependency(self):
        with self.assertRaisesRegex(m.Hold, "Required dependency absent"):
            self.guard("wallet", "#blake:1:1", metadata({"tor": dependency()}))

    def test_required_cycle(self):
        candidate = metadata({"wallet": dependency()})
        with self.assertRaisesRegex(m.Hold, "cycle"):
            self.guard("node", "#knots:29:1", candidate)

    def test_migration_denied(self):
        candidate = metadata()
        candidate["sourceVersion"] = ">=#knots:30:0"
        with self.assertRaisesRegex(m.Hold, "migration"):
            self.guard("node", "#knots:29:1", candidate)

    def test_unrelated_app_does_not_block(self):
        self.catalog["packages"]["other"] = {"versions": {"#other:1:0": metadata({"missing": dependency(None)})}}
        self.selected["other"] = "#other:1:0"
        self.guard("wallet", "#blake:1:1", self.entries["wallet"][1])

    def test_declared_satisfies_alias(self):
        candidate = metadata()
        candidate["satisfies"] = ["#knots:29:0"]
        self.guard("node", "#knots:30:0", candidate)


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.policy = {"flavor": "x", "upstream": "owner/app", "metadataRepos": ["owner/wrapper"],
                       "assetRepos": ["owner/wrapper"], "approvedOsVersions": ["0.4.0-beta.10"],
                       "trustedSignerKeys": ["pinned-key"]}
        self.meta = {"upstreamRepo": "https://github.com/owner/app", "packageRepo": "https://github.com/owner/wrapper",
                     "osVersion": "0.4.0-beta.10", "s9pks": [[{"device": [], "arch": None, "ram": None},
                         {"urls": ["https://github.com/owner/wrapper/releases/download/v1/app.s9pk"],
                          "commitment": {"rootSighash": "example", "rootMaxsize": 515},
                          "signatures": {"pinned-key": "signature"}}]]}

    def guard(self):
        m.metadata_guard("app", "#x:1:0", self.meta, self.policy)

    def test_baseline_passes(self):
        self.guard()

    def test_signer_change_holds(self):
        self.meta["s9pks"][0][1]["signatures"] = {"new-key": "signature"}
        with self.assertRaisesRegex(m.Hold, "signing key"):
            self.guard()

    def test_os_change_holds(self):
        self.meta["osVersion"] = "0.5.0"
        with self.assertRaisesRegex(m.Hold, "StartOS requirement"):
            self.guard()

    def test_fork_change_holds(self):
        self.meta["upstreamRepo"] = "https://github.com/other/app"
        with self.assertRaisesRegex(m.Hold, "identity"):
            self.guard()

    def test_asset_source_change_holds(self):
        self.meta["s9pks"][0][1]["urls"] = ["https://github.com/other/wrapper/releases/download/v1/app.s9pk"]
        with self.assertRaisesRegex(m.Hold, "asset repository"):
            self.guard()

    def test_hardware_regression_holds(self):
        old = copy.deepcopy(self.meta)
        self.meta["s9pks"][0][0]["arch"] = ["aarch64"]
        with self.assertRaisesRegex(m.Hold, "hardware"):
            m.metadata_guard("app", "#x:1:1", self.meta, self.policy, old)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = m.State(self.tmp.name)
        self.addCleanup(self.state.db.close)
        self.monitor = m.Monitor({"cli": "start-cli", "destination": "https://example.com"}, self.state)

    def test_event_deduplication(self):
        self.state.event("held", ["app", "#x:2:0"], {"message": "first"})
        self.state.event("held", ["app", "#x:2:0"], {"message": "second"})
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_delivery_failure_keeps_pending(self):
        self.state.event("held", "example", {"message": "example", "link": ""})
        with patch.dict(m.os.environ, {"NTFY_URL": "https://ntfy.sh/example"}), patch.object(
                m.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.monitor.flush_notifications()
        self.assertIsNone(self.state.db.execute("SELECT delivered FROM events").fetchone()[0])

    def test_successful_delivery_is_not_repeated(self):
        self.state.event("held", "example", {"message": "example", "link": ""})
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id":"ack"}'
        with patch.dict(m.os.environ, {"NTFY_URL": "https://ntfy.sh/example"}), patch.object(
                m.urllib.request, "urlopen", return_value=response) as post:
            self.monitor.flush_notifications()
            self.monitor.flush_notifications()
        self.assertEqual(post.call_count, 1)

    def test_dry_run_never_enqueues_or_posts(self):
        self.monitor.dry_run = True
        with patch.object(m.urllib.request, "urlopen") as post:
            self.monitor.notify("held", "example", "example")
            self.monitor.flush_notifications()
        self.assertFalse(post.called)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)

    def test_missing_digest_holds_before_download(self):
        with patch.object(m.urllib.request, "urlopen") as download, self.assertRaisesRegex(m.Hold, "digest"):
            self.monitor.inspect_download("https://example.com/file", {"size": 1}, self.tmp.name)
        self.assertFalse(download.called)

    def test_release_pagination(self):
        with patch.object(self.monitor, "github", side_effect=[[{"draft": False}] * 100, []]) as request:
            self.assertEqual(len(self.monitor.releases("owner/repo")), 100)
        self.assertEqual(request.call_count, 2)

    def test_pending_promotion_reconciles(self):
        meta = {"s9pks": [[{}, {"urls": ["example"], "commitment": {"hash": "original"}}]]}
        with self.state.db:
            self.state.db.execute("INSERT INTO promotions(package,version,evidence,status) VALUES (?,?,?,?)",
                                  ("app", "#x:2:0", json.dumps({"assets": meta["s9pks"]}), "pending"))
        self.monitor.reconcile(catalog({"app": ("#x:2:0", meta)}))
        self.assertEqual(self.state.db.execute("SELECT status FROM promotions").fetchone()[0], "verified")

    def test_pending_mismatch_holds(self):
        with self.state.db:
            self.state.db.execute("INSERT INTO promotions(package,version,evidence,status) VALUES (?,?,?,?)",
                                  ("app", "#x:2:0", json.dumps({"commitment": {"hash": "original"}}), "pending"))
        meta = {"s9pks": [[{}, {"urls": ["example"], "commitment": {"hash": "different"}}]]}
        with self.assertRaisesRegex(m.Hold, "conflicting"):
            self.monitor.reconcile(catalog({"app": ("#x:2:0", meta)}))

    def test_exact_inspected_file_is_signed_and_audited(self):
        meta = {"s9pks": [[{}, {"urls": ["https://example.com/file"], "commitment": {"hash": "original"}}]]}
        before = catalog({"app": ("#x:1:0", meta)})
        after = copy.deepcopy(before)
        after["packages"]["app"]["versions"]["#x:2:0"] = meta
        with patch.object(self.monitor, "verify_downloads", return_value=[(Path("/verified/package.s9pk"), ["https://example.com/file"])]), \
                patch.object(self.monitor, "index", side_effect=[before, after]), patch.object(self.monitor, "cli") as cli:
            self.monitor.promote("app", "#x:2:0", meta, "source", before)
        self.assertEqual(cli.call_args.args[:4], ("registry", "package", "add", "/verified/package.s9pk"))
        self.assertTrue(cli.call_args.kwargs["signed"])
        self.assertEqual(self.state.db.execute("SELECT status FROM promotions").fetchone()[0], "verified")

    def test_concurrent_catalog_change_holds_before_signing(self):
        meta = {"s9pks": []}
        before = catalog({"app": ("#x:1:0", meta)})
        with patch.object(self.monitor, "verify_downloads", return_value=[]), \
                patch.object(self.monitor, "index", return_value={"packages": {}}), patch.object(self.monitor, "cli") as cli:
            with self.assertRaisesRegex(m.Hold, "catalog changed"):
                self.monitor.promote("app", "#x:2:0", meta, "source", before)
        self.assertFalse(cli.called)


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = m.State(self.tmp.name)
        self.addCleanup(self.state.db.close)
        config = Path(self.tmp.name) / "policy.json"
        config.write_text('{"intervalSeconds":21600}')
        self.argv = ["healthcheck", "--config", str(config), "--state-dir", self.tmp.name]

    def check(self, inactive=False):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id":"ack"}'
        with patch.object(sys, "argv", self.argv), patch.object(h.subprocess, "run") as unit, \
                patch.dict(h.os.environ, {"NTFY_URL": "https://ntfy.sh/example"}), \
                patch.object(h.urllib.request, "urlopen", return_value=response) as post:
            unit.return_value.returncode = 1 if inactive else 0
            h.main()
            return post.call_count

    def test_healthy_watchdog_is_silent(self):
        self.state.put("lastSuccess", m.now())
        self.assertEqual(self.check(), 0)

    def test_initial_scan_has_grace(self):
        self.state.put("initializedAt", m.now())
        self.assertEqual(self.check(), 0)

    def test_inactive_service_alert_is_deduplicated(self):
        self.state.put("lastSuccess", m.now())
        self.assertEqual(self.check(inactive=True), 1)
        self.assertEqual(self.check(inactive=True), 0)

    def test_stale_scan_alert(self):
        self.state.put("lastSuccess", "2020-01-01T00:00:00+00:00")
        self.assertEqual(self.check(), 1)


if __name__ == "__main__":
    unittest.main()
