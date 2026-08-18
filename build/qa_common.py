"""Shared configuration and helpers for the qanda build scripts.

generate.py and extract_media.py both connect to the same database and write
under the same assets/ tree, so that setup lives here to avoid drift.
"""
import os
import time
from pathlib import Path

import pymysql

PROJECT = Path(__file__).resolve().parent.parent
ASSETS = PROJECT / "assets"
AVATARS = ASSETS / "avatars"
MEDIA = ASSETS / "media"

# Contributors who asked for their picture to be removed. One userid or handle per
# line, "#" comments allowed. Honoured by both extract_media.py (never fetch) and
# generate.py (never render), so a removal survives future rebuilds.
REMOVED_LIST = AVATARS / "removed.txt"

PREFIX = os.environ.get("QA_TABLE_PREFIX", "qa_")
DB = dict(
    host=os.environ.get("QA_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("QA_DB_PORT", "3307")),
    user=os.environ.get("QA_DB_USER", "root"),
    password=os.environ.get("QA_DB_PASSWORD", "root"),
    database=os.environ.get("QA_DB_NAME", "qanda"),
)


def connect_with_retry(timeout=90):
    """Wait for the database to accept connections and finish loading the dump.

    In the compose pipeline the db service may still be importing the dump when a
    build script starts, so we retry until the posts table has rows. Any connection
    opened on a failed attempt is closed before retrying."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        conn = None
        try:
            conn = pymysql.connect(charset="utf8mb4", connect_timeout=5, **DB)
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {PREFIX}posts")
                if cur.fetchone()[0] > 0:
                    return conn
            conn.close()
        except pymysql.Error as exc:
            last = exc
            if conn is not None:
                try:
                    conn.close()
                except pymysql.Error:
                    pass
        time.sleep(2)
    raise SystemExit(f"database not ready after {timeout}s: {last}")


def load_removed():
    """Set of lowercased userids/handles whose avatar must never be published."""
    if not REMOVED_LIST.exists():
        return set()
    entries = set()
    for line in REMOVED_LIST.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip().lower()
        if entry:
            entries.add(entry)
    return entries


def is_removed(removed, userid, handle):
    return bool(removed) and (str(userid).lower() in removed
                              or (handle or "").strip().lower() in removed)
