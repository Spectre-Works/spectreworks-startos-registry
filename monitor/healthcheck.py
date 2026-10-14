#!/usr/bin/env python3
"""Independent watchdog: deliberately does not import the release monitor."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request
import urllib.parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    db = sqlite3.connect(Path(args.state_dir) / "state.sqlite3")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
            fingerprint TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL,
            created TEXT NOT NULL, delivered TEXT, lease_until REAL NOT NULL DEFAULT 0);
    """)
    if "receipt" not in {row[1] for row in db.execute("PRAGMA table_info(events)")}:
        db.execute("ALTER TABLE events ADD COLUMN receipt TEXT")
        db.commit()

    def read(key):
        row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    last = read("lastSuccess")
    initialized = read("initializedAt") or read("lastAttempt")
    utc = dt.datetime.now(dt.timezone.utc)
    stale = not last or (utc - dt.datetime.fromisoformat(last)).total_seconds() > config.get("intervalSeconds", 21600) + 3600
    if not last and initialized and (utc - dt.datetime.fromisoformat(initialized)).total_seconds() < 1800:
        stale = False
    inactive = subprocess.run(["systemctl", "is-active", "--quiet", "startos-upstream-monitor.service"]).returncode != 0
    problem = "service inactive" if inactive else ("no complete upstream check within seven hours" if stale else None)
    if problem and read("healthProblem") != problem:
        ident = hashlib.sha256(json.dumps(["watchdog", problem, last]).encode()).hexdigest()
        body = json.dumps({"message": "StartOS monitor health: " + problem, "link": ""})
        with db:
            db.execute("INSERT OR IGNORE INTO events(fingerprint,kind,body,created) VALUES (?,?,?,?)",
                       (ident, "failure", body, utc.isoformat()))
    with db:
        db.execute("INSERT OR REPLACE INTO kv VALUES ('healthProblem',?)", (json.dumps(problem),))
    # Deliver all failure events even when the main process cannot start.
    for ident, raw in db.execute("SELECT fingerprint,body FROM events WHERE kind='failure' AND delivered IS NULL").fetchall():
        with db:
            claimed = db.execute("UPDATE events SET lease_until=? WHERE fingerprint=? AND delivered IS NULL AND lease_until<=?",
                                 (time.time() + 120, ident, time.time())).rowcount
        if not claimed:
            continue
        try:
            url = os.environ["NTFY_URL"]
            if not url.startswith("https://ntfy.sh/"):
                raise ValueError("Invalid ntfy endpoint")
            headers = {"Title": "StartOS: monitor failure", "Priority": "4", "Tags": "warning"}
            if os.environ.get("NTFY_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"]
            body = json.loads(raw)
            req = urllib.request.Request(url, data=body["message"].encode(), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read(65536))
                if (not result.get("id") or result.get("event") != "message" or
                        result.get("topic") != urllib.parse.urlparse(url).path.strip("/")):
                    raise ValueError("ntfy did not acknowledge the configured topic")
            with db:
                receipt = {k: result.get(k) for k in ("id", "event", "topic", "time")}
                db.execute("UPDATE events SET delivered=?,receipt=? WHERE fingerprint=?",
                           (utc.isoformat(), json.dumps(receipt), ident))
        except Exception as error:
            with db:
                db.execute("UPDATE events SET lease_until=0 WHERE fingerprint=?", (ident,))
            print("ntfy watchdog delivery failed; retained for retry:", type(error).__name__)
    db.close()


if __name__ == "__main__":
    main()
