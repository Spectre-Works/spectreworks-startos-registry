#!/usr/bin/env python3
"""StartOS upstream monitor. Standard library only; never executes package code."""
import argparse
import contextlib
import datetime as dt
import fcntl
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger("startos-upstream")
HERE = Path(__file__).resolve().parent


class Hold(Exception):
    """Candidate cannot be safely promoted."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def exver(op, **args):
    p = subprocess.run(["node", str(HERE / "exver.cjs")], input=canonical({"op": op, **args}),
                       text=True, capture_output=True, timeout=15)
    if p.returncode:
        raise Hold("Unrecognized StartOS version/range: " + p.stderr[:200])
    return json.loads(p.stdout)


@lru_cache(maxsize=2048)
def parts(version):
    return exver("parts", versions=[version])[0]


@lru_cache(maxsize=2048)
def matches(version, version_range):
    if not isinstance(version_range, str) or not version_range:
        raise Hold("Missing version constraint")
    return exver("matches", checks=[[version, version_range]])[0]


def repository(url):
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname != "github.com" or p.username or p.password:
        raise Hold("Unapproved repository URL")
    fields = p.path.strip("/").removesuffix(".git").split("/")
    if len(fields) != 2:
        raise Hold("Repository URL must identify one GitHub repository")
    return "/".join(fields).lower()


def asset_repository(url):
    p = urllib.parse.urlparse(url)
    fields = p.path.strip("/").split("/")
    if (p.scheme != "https" or p.hostname != "github.com" or p.username or p.password
            or len(fields) < 6 or fields[2:4] != ["releases", "download"]
            or not fields[-1].endswith(".s9pk") or p.query or p.fragment):
        raise Hold("Asset is not an approved GitHub Release .s9pk URL")
    return "/".join(fields[:2]).lower()


def release_version(policy, tag):
    m = re.fullmatch(policy["releasePattern"], tag)
    if not m:
        raise Hold("Unrecognized upstream release tag " + tag)
    return m.group("version")


def latest(versions, flavor):
    eligible = [v for v in versions if parts(v)["flavor"] == flavor]
    return exver("sort", versions=eligible)[-1] if eligible else None


def covers(new, old):
    """Mirror StartOS hardware coverage rules; unknown restrictions hold."""
    return ((new.get("arch") is None or
             (old.get("arch") is not None and set(old["arch"]) <= set(new["arch"])))
            and (new.get("ram") is None or
                 (old.get("ram") is not None and new["ram"] <= old["ram"]))
            and set(new.get("device", [])) <= set(old.get("device", [])))


def metadata_guard(package_id, version, meta, policy, current_meta=None, indexed=True):
    if parts(version)["flavor"] != policy["flavor"]:
        raise Hold("Package flavor changed")
    if repository(meta["upstreamRepo"]) != policy["upstream"].lower():
        raise Hold("Application upstream identity changed")
    if repository(meta["packageRepo"]) not in {p.lower() for p in policy["metadataRepos"]}:
        raise Hold("Packaging repository identity changed")
    if meta.get("osVersion") not in policy["approvedOsVersions"]:
        raise Hold("StartOS requirement changed to " + str(meta.get("osVersion")))
    if not meta.get("s9pks"):
        raise Hold("No package artifacts")
    for hw, artifact in meta["s9pks"]:
        if not isinstance(hw, dict) or not artifact.get("urls") or not artifact.get("commitment"):
            raise Hold("Incomplete artifact metadata")
        for url in artifact["urls"]:
            if asset_repository(url) not in {p.lower() for p in policy["assetRepos"]}:
                raise Hold("Package asset repository changed")
        if indexed and not set(artifact.get("signatures", {})) & set(policy["trustedSignerKeys"]):
            raise Hold("Upstream package signing key is unknown")
    if current_meta:
        for old, _ in current_meta["s9pks"]:
            if not any(covers(new, old) for new, _ in meta["s9pks"]):
                raise Hold("Candidate drops a previously supported hardware target")
        for feature in ("hardwareAcceleration", "userspaceFilesystems", "virtualNetworking",
                        "hardwareVirtualization", "plugins", "satisfies"):
            if meta.get(feature) != current_meta.get(feature):
                raise Hold("Runtime capability changed: " + feature)
    if meta.get("preDownloadAlert"):
        raise Hold("Upstream package requests a pre-download operator warning")


def dependency_guard(catalog, selected, package_id, version, meta):
    """Validate affected dependency component, including reverse consumers.

    A null legacy versionRange means UNKNOWN, not unconstrained. Packages with
    runtime-only dependency declarations are held until upstream publishes ranges.
    """
    before = {p: catalog["packages"][p]["versions"][v] for p, v in selected.items()}
    proposed = dict(before)
    proposed[package_id] = meta
    versions = dict(selected, **{package_id: version})
    if not matches(selected[package_id], meta.get("sourceVersion")):
        raise Hold("No declared migration path from " + selected[package_id])
    affected = {package_id}
    # First identify consumers whose provider is being replaced, recursively.
    while True:
        expanded = set(affected)
        for p, m in proposed.items():
            deps = m.get("dependencyMetadata")
            if not isinstance(deps, dict):
                raise Hold("Missing dependency metadata for " + p)
            if set(deps) & affected:
                expanded.add(p)
        if expanded == affected:
            break
        affected = expanded
    # Then follow their dependencies transitively. Do not pull in every other
    # consumer of an unchanged provider: it is not part of this update.
    while True:
        expanded = affected | {d for p in affected for d in proposed[p]["dependencyMetadata"] if d in proposed}
        if expanded == affected:
            break
        affected = expanded
    edges = {p: [] for p in affected}
    for p in sorted(affected):
        for dep, info in proposed[p]["dependencyMetadata"].items():
            if not isinstance(info, dict) or not isinstance(info.get("optional"), bool):
                raise Hold("Incomplete dependency declaration: " + p + " -> " + dep)
            if dep not in versions:
                if info["optional"]:
                    continue
                raise Hold("Required dependency absent: " + p + " -> " + dep)
            if info.get("versionRange") is None:
                raise Hold("Dependency range unknown: " + p + " -> " + dep)
            if not matches(versions[dep], info["versionRange"]):
                # The StartOS satisfies aliases are supported explicitly.
                aliases = proposed[dep].get("satisfies", [])
                if not any(matches(a, info["versionRange"]) for a in aliases):
                    raise Hold(f"Dependency conflict: {p} needs {dep} {info['versionRange']}; "
                               f"proposed {versions[dep]}")
            if not info["optional"]:
                edges[p].append(dep)
    visiting, visited = set(), set()

    def visit(p):
        if p in visiting:
            raise Hold("Required dependency cycle involving " + p)
        if p in visited:
            return
        visiting.add(p)
        for dep in edges.get(p, []):
            visit(dep)
        visiting.remove(p)
        visited.add(p)
    for p in affected:
        visit(p)


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "state.sqlite3")
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                fingerprint TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL,
                created TEXT NOT NULL, delivered TEXT);
            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY, package TEXT, version TEXT, source TEXT,
                evidence TEXT, status TEXT, created TEXT, updated TEXT);
            CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, etag TEXT, body TEXT);
        """)
        if "lease_until" not in {row[1] for row in self.db.execute("PRAGMA table_info(events)")}:
            self.db.execute("ALTER TABLE events ADD COLUMN lease_until REAL NOT NULL DEFAULT 0")
            self.db.commit()
        if "receipt" not in {row[1] for row in self.db.execute("PRAGMA table_info(events)")}:
            self.db.execute("ALTER TABLE events ADD COLUMN receipt TEXT")
            self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, canonical(value)))

    def event(self, kind, identity, body):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO events(fingerprint,kind,body,created,delivered) VALUES (?,?,?,?,NULL)",
                            (fingerprint([kind, identity]), kind, canonical(body), now()))


class Monitor:
    def __init__(self, config, state, dry_run=False):
        self.config, self.state, self.dry_run = config, state, dry_run
        self.release_cache = {}
        self.cli_path = config["cli"]
        self.key = os.environ.get("STARTOS_SIGNING_KEY")
        if not self.key and os.environ.get("CREDENTIALS_DIRECTORY"):
            self.key = str(Path(os.environ["CREDENTIALS_DIRECTORY"]) / "signing-key")

    def cli(self, *args, registry=None, signed=False, timeout=180):
        cmd = [self.cli_path]
        if registry:
            cmd += ["--registry", registry]
        if signed:
            if not self.key:
                raise Hold("Service signing credential is unavailable")
            cmd += ["--id-key-path", self.key]
        cmd += list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Hold("start-cli operation timed out") from None
        if p.returncode:
            # CLI output may contain environment-specific data: journal only the command stage.
            raise Hold("start-cli failed at " + " ".join(args[:3]) + f" (exit {p.returncode})")
        return p.stdout

    def index(self, url):
        x = json.loads(self.cli("registry", "package", "index", "--format", "json", registry=url))
        if not isinstance(x.get("packages"), dict):
            raise Hold("Registry returned an invalid catalog")
        return x

    def github(self, url):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "SPECTRE-WORKS-StartOS-Monitor",
                   "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        cached = self.state.db.execute("SELECT etag,body FROM http_cache WHERE url=?", (url,)).fetchone()
        if cached and cached[0]:
            headers["If-None-Match"] = cached[0]
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as r:
                raw = r.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise Hold("GitHub response exceeds size limit")
                value = json.loads(raw)
                with self.state.db:
                    self.state.db.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?,?)",
                                          (url, r.headers.get("ETag"), canonical(value)))
                return value
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                return json.loads(cached[1])
            raise Hold(f"GitHub API HTTP {e.code}; source {urllib.parse.urlparse(url).path}") from None

    def releases(self, repo):
        if repo.lower() in self.release_cache:
            return self.release_cache[repo.lower()]
        releases = []
        # Bounded pagination: if the bound is reached, hold rather than miss releases.
        for page in range(1, 21):
            batch = self.github(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise Hold("Invalid GitHub release response")
            releases.extend(r for r in batch if not r.get("draft"))
            if len(batch) < 100:
                self.release_cache[repo.lower()] = releases
                return releases
        raise Hold("Release pagination limit reached for " + repo)

    def notify(self, kind, identity, message, link=""):
        LOG.info("%s: %s", kind, message)
        if not self.dry_run:
            self.state.event(kind, identity, {"message": message, "link": link})

    def flush_notifications(self):
        if self.dry_run:
            return
        url = os.environ.get("NTFY_URL")
        if not url or not url.startswith("https://ntfy.sh/"):
            raise Hold("NTFY_URL credential is missing or invalid")
        rows = self.state.db.execute(
            "SELECT fingerprint,kind,body FROM events WHERE delivered IS NULL ORDER BY created LIMIT 50").fetchall()
        titles = {"packaging_required": "StartOS: packaging required", "held": "StartOS: update held",
                  "failure": "StartOS: monitor failure", "unmapped": "StartOS: app needs source mapping",
                  "updated": "StartOS: package updated"}
        for ident, kind, body in rows:
            # The independent watchdog shares this outbox; claim delivery to avoid
            # two healthy processes posting the same alert concurrently.
            with self.state.db:
                claimed = self.state.db.execute(
                    "UPDATE events SET lease_until=? WHERE fingerprint=? AND delivered IS NULL AND lease_until<=?",
                    (time.time() + 120, ident, time.time())).rowcount
            if not claimed:
                continue
            event = json.loads(body)
            headers = {"Title": titles.get(kind, "StartOS registry monitor"),
                       "Priority": "3" if kind == "updated" else "4",
                       "Tags": "package,white_check_mark" if kind == "updated" else "package,warning"}
            if event["link"]:
                headers["Click"] = event["link"]
            if os.environ.get("NTFY_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"]
            try:
                req = urllib.request.Request(url, data=event["message"].encode(), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30) as r:
                    result = json.loads(r.read(65536))
                    if (not result.get("id") or result.get("event") != "message" or
                            result.get("topic") != urllib.parse.urlparse(url).path.strip("/")):
                        raise Hold("ntfy did not acknowledge publication to the configured topic")
                with self.state.db:
                    receipt = {k: result.get(k) for k in ("id", "event", "topic", "time")}
                    self.state.db.execute("UPDATE events SET delivered=?,receipt=? WHERE fingerprint=?",
                                          (now(), canonical(receipt), ident))
            except Exception:
                with self.state.db:
                    self.state.db.execute("UPDATE events SET lease_until=0 WHERE fingerprint=?", (ident,))
                LOG.error("ntfy delivery failed; event retained for retry")
                break

    def asset_release(self, url):
        repo = asset_repository(url)
        path = urllib.parse.urlparse(url).path.strip("/").split("/")
        tag = urllib.parse.unquote("/".join(path[4:-1]))
        for release in self.releases(repo):
            if release["tag_name"] == tag:
                for asset in release["assets"]:
                    if asset["browser_download_url"] == url:
                        return release, asset
        raise Hold("Indexed package asset is absent from its GitHub Release")

    def inspect_download(self, url, asset, directory):
        digest = asset.get("digest")
        if not digest or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise Hold("GitHub asset has no verifiable SHA-256 digest")
        maximum = self.config.get("maxAssetBytes", 2 * 1024**3)
        if not 0 < asset.get("size", 0) <= maximum:
            raise Hold("Asset exceeds download limit")
        destination = Path(directory) / "package.s9pk"
        sha = hashlib.sha256()
        count = 0
        deadline = time.monotonic() + 1800
        req = urllib.request.Request(url, headers={"User-Agent": "SPECTRE-WORKS-StartOS-Monitor"})
        with urllib.request.urlopen(req, timeout=60) as r, destination.open("wb") as out:
            while chunk := r.read(1024 * 1024):
                count += len(chunk)
                if count > maximum or time.monotonic() > deadline:
                    raise Hold("Asset download exceeds resource limit")
                sha.update(chunk)
                out.write(chunk)
        if count != asset["size"] or sha.hexdigest() != digest[7:]:
            raise Hold("Downloaded asset size or SHA-256 differs from GitHub")
        manifest = json.loads(self.cli("s9pk", "inspect", str(destination), "manifest", "--format", "json"))
        commitment = json.loads(self.cli("s9pk", "inspect", str(destination), "commitment", "--format", "json"))
        return destination, manifest, commitment

    def validate_indexed(self, package_id, version, meta, policy):
        for _, artifact in meta["s9pks"]:
            for url in artifact["urls"]:
                release, _ = self.asset_release(url)
                if release["prerelease"] and not policy["allowPrereleases"]:
                    raise Hold("Package release is marked prerelease")
                if re.search(r"(?:alpha|beta|rc\d*)", release["tag_name"], re.I) and not policy["allowPrereleases"]:
                    raise Hold("Package tag names a prerelease")

    def verify_downloads(self, package_id, version, meta, directory):
        # Inspect exact release bytes before signing; promotion also verifies on the registry server.
        files = []
        for i, (_, artifact) in enumerate(meta["s9pks"]):
            url = artifact["urls"][0]
            _, asset = self.asset_release(url)
            tmp = Path(directory) / str(i)
            tmp.mkdir()
            path, manifest, commitment = self.inspect_download(url, asset, tmp)
            if manifest.get("id") != package_id or manifest.get("version") != version:
                raise Hold("Asset manifest differs from indexed identity/version")
            if commitment != artifact["commitment"]:
                raise Hold("Asset commitment differs from source registry")
            for field in ("upstreamRepo", "packageRepo", "osVersion"):
                if manifest.get(field) != meta.get(field):
                    raise Hold("Asset manifest differs from index: " + field)
            manifest_deps = manifest.get("dependencies", {})
            if set(manifest_deps) != set(meta["dependencyMetadata"]):
                raise Hold("Asset dependency set differs from index")
            for dep, info in meta["dependencyMetadata"].items():
                if any(manifest_deps[dep].get(f) != info.get(f) for f in ("optional", "versionRange")):
                    raise Hold("Asset dependency declaration differs from index")
            if manifest.get("canMigrateFrom") != meta.get("sourceVersion"):
                raise Hold("Asset migration declaration differs from index")
            files.append((path, artifact["urls"]))
        return files

    def complete_promotion(self, row, package_id, version, published, source, previous=None):
        """Atomically record verification and enqueue a recoverable success notice."""
        at = now()
        change = f"{previous} -> {version}" if previous else version
        body = {"message": f"{package_id}: registry package updated: {change}. "
                           "Publication and package commitments verified. "
                           "This updates marketplace availability; installed apps are not upgraded.",
                "link": self.config["destination"]}
        identity = [package_id, version, artifact_identity(published)]
        with self.state.db:
            self.state.db.execute("UPDATE promotions SET status='verified',updated=? WHERE id=?", (at, row))
            self.state.db.execute(
                "INSERT OR IGNORE INTO events(fingerprint,kind,body,created) VALUES (?,?,?,?)",
                (fingerprint(["updated", identity]), "updated", canonical(body), at))
            self.state.db.execute("INSERT OR REPLACE INTO kv VALUES ('lastPromotion',?)",
                                  (canonical({"package": package_id, "version": version,
                                              "previousVersion": previous, "source": source, "at": at}),))

    def promote(self, package_id, version, meta, source, catalog):
        previous = latest(catalog["packages"][package_id]["versions"], parts(version)["flavor"])
        evidence = {"catalog": fingerprint(catalog), "metadata": fingerprint(meta), "assets": meta["s9pks"],
                    "previousVersion": previous}
        with tempfile.TemporaryDirectory(prefix="asset-", dir=self.state.directory) as tmp:
            files = self.verify_downloads(package_id, version, meta, tmp)
            # Detect concurrent operator changes immediately before the signed mutation.
            if fingerprint(self.index(self.config["destination"])) != fingerprint(catalog):
                raise Hold("Destination catalog changed during preflight; retry next cycle")
            with self.state.db:
                row = self.state.db.execute(
                    "INSERT INTO promotions(package,version,source,evidence,status,created,updated) VALUES (?,?,?,?,?,?,?)",
                    (package_id, version, source, canonical(evidence), "pending", now(), now())).lastrowid
            # Index the exact inspected bytes; remote promote would re-read mutable upstream metadata.
            for path, urls in files:
                url_args = [item for url in urls for item in ("--url", url)]
                self.cli("registry", "package", "add", str(path), *url_args,
                         registry=self.config["destination"], signed=True, timeout=3600)
        result = self.index(self.config["destination"])
        published = result["packages"].get(package_id, {}).get("versions", {}).get(version)
        if not published or artifact_identity(published) != artifact_identity(meta):
            raise Hold("Promotion destination verification failed")
        self.complete_promotion(row, package_id, version, published, source, previous)
        LOG.info("Promoted and verified %s %s", package_id, version)
        return result

    def process(self, package_id, policy, catalog, sources, selected):
        current = selected[package_id]
        current_meta = catalog["packages"][package_id]["versions"][current]
        current_upstream = parts(current)["upstream"]
        release_rows = self.releases(policy["upstream"])
        # Packaging repositories can ship without the source registry indexing the release.
        packaging_rows = {repo.lower(): self.releases(repo) for repo in policy["assetRepos"]}
        candidates = {}
        for source, source_index in sources.items():
            for version, meta in source_index["packages"].get(package_id, {}).get("versions", {}).items():
                if parts(version)["flavor"] != policy["flavor"] or not matches(version, ">" + current):
                    continue
                if version in candidates and fingerprint(candidates[version][1]) != fingerprint(meta):
                    raise Hold("Source registries disagree about candidate " + version)
                candidates[version] = (source, meta)

        # Alert for every outstanding upstream release, not just GitHub's latest release.
        for release in release_rows:
            tag = release["tag_name"]
            if (release["prerelease"] or re.search(r"(?:alpha|beta|rc\d*)", tag, re.I)) and not policy["allowPrereleases"]:
                continue
            upstream_version = release_version(policy, tag)
            comparison = exver("upstreamCompare", a=upstream_version, b=current_upstream)
            if comparison < 0:
                continue
            # Same-number upstream releases can differ by build/date (notably Knots).
            if comparison == 0 and (tag == policy.get("baselineUpstreamTag") or
                    any(tag in urllib.parse.unquote(u) for _, a in current_meta["s9pks"] for u in a["urls"])):
                continue
            if comparison == 0 and release["published_at"] <= policy["baselinePublishedAt"]:
                continue
            matching = []
            for version, (_, meta) in candidates.items():
                if exver("upstreamCompare", a=parts(version)["upstream"], b=upstream_version) == 0:
                    # At equal upstream version, require actual tag evidence instead of numeric similarity.
                    if comparison > 0 or any(tag in urllib.parse.unquote(u) for _, a in meta["s9pks"] for u in a["urls"]):
                        matching.append(version)
            asset_matches = [r for rows in packaging_rows.values() for r in rows
                             if tag in r["tag_name"] and any(a["name"].endswith(".s9pk") for a in r["assets"])
                             and (policy["allowPrereleases"] or
                                  not (r["prerelease"] or re.search(r"(?:alpha|beta|rc\d*)", r["tag_name"], re.I)))]
            if not matching and not asset_matches:
                self.notify("packaging_required", [package_id, release["id"], tag],
                            f"{package_id}: upstream {tag} is available; registry has {current}. "
                            "No matching StartOS package was found in approved sources.", release["html_url"])

        # Re-evaluate held candidates every run. One update at a time, retaining all old versions.
        for version in reversed(exver("sort", versions=list(candidates))):
            source, meta = candidates[version]
            try:
                metadata_guard(package_id, version, meta, policy, current_meta)
                self.validate_indexed(package_id, version, meta, policy)
                dependency_guard(catalog, selected, package_id, version, meta)
                if self.dry_run:
                    LOG.info("Would promote %s %s after asset verification", package_id, version)
                    break
                catalog = self.promote(package_id, version, meta, source, catalog)
                selected[package_id] = version
                break
            except Hold as e:
                self.notify("held", [package_id, version, str(e)],
                            f"{package_id}: update {current} -> {version} held. {e}.",
                            meta["packageRepo"] + "/releases")

        # GitHub-only artifacts use the same signed index path, after manifest verification.
        indexed_urls = {u for _, meta in candidates.values() for _, a in meta["s9pks"] for u in a["urls"]}
        existing_urls = {u for meta in catalog["packages"][package_id]["versions"].values()
                         for _, a in meta["s9pks"] for u in a["urls"]}
        for repo, rows in packaging_rows.items():
            for release in rows:
                if release["published_at"] <= policy["baselinePublishedAt"]:
                    continue
                if release["prerelease"] and not policy["allowPrereleases"]:
                    continue
                if re.search(r"(?:alpha|beta|rc\d*)", release["tag_name"], re.I) and not policy["allowPrereleases"]:
                    continue
                for asset in release["assets"]:
                    url = asset["browser_download_url"]
                    if not asset["name"].endswith(".s9pk") or url in existing_urls or url in indexed_urls:
                        continue
                    try:
                        asset_repository(url)
                        if self.dry_run:
                            LOG.info("GitHub-only artifact to inspect: %s", url)
                            continue
                        with tempfile.TemporaryDirectory(prefix="asset-", dir=self.state.directory) as tmp:
                            path, manifest, commitment = self.inspect_download(url, asset, tmp)
                            version = manifest.get("version")
                            if manifest.get("id") != package_id:
                                raise Hold("Release artifact package ID differs from configured app")
                            if not matches(version, ">" + selected[package_id]):
                                continue
                            meta = manifest_metadata(manifest, url, commitment)
                            # Explicit repository + GitHub digest trust for unindexed assets.
                            if not policy.get("allowGithubOnly", False):
                                raise Hold("GitHub-only artifact not approved by package policy")
                            metadata_guard(package_id, version, meta, policy, current_meta, indexed=False)
                            dependency_guard(catalog, selected, package_id, version, meta)
                            if fingerprint(self.index(self.config["destination"])) != fingerprint(catalog):
                                raise Hold("Destination catalog changed during preflight")
                            with self.state.db:
                                row = self.state.db.execute(
                                    "INSERT INTO promotions(package,version,source,evidence,status,created,updated) VALUES (?,?,?,?,?,?,?)",
                                    (package_id, version, url, canonical({"sha256": asset["digest"], "commitment": commitment,
                                                                       "previousVersion": selected[package_id]}),
                                     "pending", now(), now())).lastrowid
                            self.cli("registry", "package", "add", str(path), "--url", url,
                                     registry=self.config["destination"], signed=True, timeout=3600)
                            catalog = self.index(self.config["destination"])
                            found = catalog["packages"][package_id]["versions"].get(version)
                            if not found or artifact_identity(found) != artifact_identity(meta):
                                raise Hold("GitHub-only promotion destination verification failed")
                            self.complete_promotion(row, package_id, version, found, url, selected[package_id])
                            selected[package_id] = version
                            LOG.info("Registered and verified %s %s", package_id, version)
                    except Hold as e:
                        self.notify("held", [package_id, asset["id"], asset.get("digest"), str(e)],
                                    f"{package_id}: GitHub-only package {release['tag_name']} held. {e}.", release["html_url"])
        return catalog

    def reconcile(self, catalog):
        rows = self.state.db.execute("SELECT id,package,version,evidence,source FROM promotions WHERE status='pending' ORDER BY id").fetchall()
        for row, package, version, evidence, source in rows:
            published = catalog["packages"].get(package, {}).get("versions", {}).get(version)
            expected = json.loads(evidence)
            if published and (("assets" in expected and artifact_identity(published) ==
                               artifact_identity({"s9pks": expected["assets"]})) or
                              ("commitment" in expected and any(a["commitment"] == expected["commitment"]
                                                               for _, a in published["s9pks"]))):
                self.complete_promotion(row, package, version, published, source, expected.get("previousVersion"))
            elif published:
                raise Hold("Interrupted promotion has conflicting destination commitment: " + package + " " + version)
            else:
                self.notify("failure", ["interrupted", row], f"Interrupted promotion of {package} {version}; "
                            "destination entry absent. Monitor will retry after preflight.")
                with self.state.db:
                    self.state.db.execute("UPDATE promotions SET status='retry',updated=? WHERE id=?", (now(), row))

    def cycle(self):
        self.release_cache.clear()
        self.state.put("lastAttempt", now())
        catalog = self.index(self.config["destination"])
        if not self.dry_run:
            self.reconcile(catalog)
        sources = {url: self.index(url) for url in self.config["sources"]}
        selected = {}
        for package_id, entry in catalog["packages"].items():
            policy = self.config["packages"].get(package_id)
            if policy is None:
                self.notify("unmapped", package_id, package_id + ": registered app has no approved source mapping; add policy and signer scope.")
                # Include unconfigured reverse dependencies in the compatibility graph.
                flavors = {parts(v)["flavor"] for v in entry["versions"]}
                if len(flavors) != 1:
                    raise Hold("Unmapped app has ambiguous active flavor: " + package_id)
                selected[package_id] = latest(entry["versions"], next(iter(flavors)))
            else:
                selected[package_id] = latest(entry["versions"], policy["flavor"])
            if selected[package_id] is None:
                raise Hold("No registered version of configured flavor for " + package_id)
        failed = []
        for package_id in sorted(catalog["packages"]):
            if package_id not in self.config["packages"]:
                continue
            try:
                catalog = self.process(package_id, self.config["packages"][package_id], catalog, sources, selected)
                self.state.put("failures:" + package_id, 0)
            except Exception as e:
                failed.append(package_id)
                count = self.state.get("failures:" + package_id, 0) + 1
                self.state.put("failures:" + package_id, count)
                episode_key = "failureEpisode:" + package_id
                if count == 1 or not self.state.get(episode_key):
                    self.state.put(episode_key, now())
                reason = str(e) if isinstance(e, Hold) else type(e).__name__
                LOG.error("%s check failed: %s", package_id, reason)
                if count > 3:
                    self.notify("failure", [package_id, self.state.get(episode_key), reason],
                                f"{package_id}: upstream checks failed for {count} consecutive runs. {reason}.")
        self.state.put("catalogSnapshot", catalog)
        self.state.put("lastCompleted", now())
        self.state.put("lastResult", {"failedPackages": failed, "dryRun": self.dry_run})
        if not failed:
            self.state.put("lastSuccess", now())
        self.flush_notifications()
        LOG.info("Cycle complete: %d apps; %d source failures", len(selected), len(failed))
        return not failed


def artifact_identity(meta):
    return sorted(canonical({"requirements": hw, "urls": sorted(a["urls"]), "commitment": a["commitment"]})
                  for hw, a in meta["s9pks"])


def manifest_metadata(manifest, url, commitment):
    meta = {k: v for k, v in manifest.items() if k not in
            ("id", "version", "images", "volumes", "dependencies", "hardwareRequirements", "canMigrateTo", "canMigrateFrom")}
    meta["dependencyMetadata"] = manifest.get("dependencies", {})
    meta["sourceVersion"] = manifest.get("canMigrateFrom")
    meta["s9pks"] = [[manifest.get("hardwareRequirements", {"device": [], "ram": None, "arch": None}),
                      {"urls": [url], "commitment": commitment}]]
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="No promotions or ntfy writes; use separate state directory")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(Path(args.config).read_text())
    state = State(args.state_dir)
    monitor = Monitor(config, state, args.dry_run)
    lock = (state.directory / "monitor.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.exit(1, "Another monitor instance is running\n")
    if not state.get("initializedAt"):
        state.put("initializedAt", now())
    for path in state.directory.glob("asset-*"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)  # Interrupted downloads only; the process lock is held.
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        success = False
        try:
            success = monitor.cycle()
            state.put("cycleFailures", 0)
        except Exception as e:
            count = state.get("cycleFailures", 0) + 1
            state.put("cycleFailures", count)
            if count == 1 or not state.get("cycleFailureEpisode"):
                state.put("cycleFailureEpisode", now())
            reason = str(e) if isinstance(e, Hold) else type(e).__name__
            LOG.error("Cycle failed: %s", reason)
            if count > 3:
                monitor.notify("failure", ["cycle", state.get("cycleFailureEpisode"), reason],
                               f"StartOS monitor failed for {count} consecutive cycles. {reason}.")
            with contextlib.suppress(Exception):
                monitor.flush_notifications()
        if args.once:
            return 0 if success else 1
        next_check = time.time() + (config.get("intervalSeconds", 21600) if success else config.get("retrySeconds", 900))
        state.put("nextCheck", dt.datetime.fromtimestamp(next_check, dt.timezone.utc).isoformat())
        # Wake each minute to retry undelivered notifications; stop promptly on SIGTERM.
        while not stop.wait(min(60, max(0, next_check - time.time()))):
            with contextlib.suppress(Exception):
                monitor.flush_notifications()
            if time.time() >= next_check:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
