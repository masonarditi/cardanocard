"""Durable records for one service process. Side effects are checkpointed first."""
import fcntl
import json
import sqlite3
import time
from contextlib import suppress
from pathlib import Path

from .models import canonical


class Store:
    def __init__(self, path: str):
        self.event_sink = None
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = open(str(self.path) + ".lockfile", "a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise RuntimeError("Database already owned by another process; use one worker") from None
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                namespace TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(namespace, key));
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, job_id TEXT NOT NULL,
                created_at REAL NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL);
        """)
        self.path.chmod(0o600)

    def get(self, namespace, key):
        row = self.db.execute("SELECT data FROM records WHERE namespace=? AND key=?", (namespace, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, namespace, key, data):
        with self.db:
            self.db.execute("INSERT INTO records VALUES(?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET data=excluded.data",
                            (namespace, key, canonical(data)))

    def jobs(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM records WHERE namespace='jobs'")]

    def save_job(self, job, message):
        created_at = time.time()
        with self.db:
            self.db.execute("INSERT INTO records VALUES('jobs',?,?) ON CONFLICT(namespace,key) DO UPDATE SET data=excluded.data",
                            (job["id"], canonical(job)))
            cursor = self.db.execute("INSERT INTO events(job_id,created_at,state,message) VALUES(?,?,?,?)",
                                     (job["id"], created_at, job["phase"], message))
        if self.event_sink:
            # Visibility cannot roll back a committed checkpoint or interrupt purchasing.
            with suppress(Exception):
                self.event_sink(self._event((cursor.lastrowid, job["id"], created_at, job["phase"], message), job))

    @staticmethod
    def _event(row, job):
        return dict(sequence=row[0], job_id=row[1], at=row[2], phase=row[3], message=row[4],
                    simulated_escrow=job["simulated_escrow"], simulated_purchase=job["simulated_purchase"])

    def event_feed(self, after=None, limit=20):
        latest = self.db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
        if after is not None and after > latest:
            raise ValueError("Event cursor is ahead of this database; restart the feed without --after")
        if after is None:
            rows = list(self.db.execute(
                "SELECT sequence,job_id,created_at,state,message FROM events ORDER BY sequence DESC LIMIT ?", (limit,)))[::-1]
        else:
            rows = list(self.db.execute(
                "SELECT sequence,job_id,created_at,state,message FROM events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)))
        events = [self._event(row, self.get("jobs", row[1])) for row in rows]
        return {"events": events, "cursor": rows[-1][0] if rows else (after or latest), "latest": latest}

    def events(self, job_id):
        return [{"at": r[0], "phase": r[1], "message": r[2]} for r in self.db.execute(
            "SELECT created_at,state,message FROM events WHERE job_id=? ORDER BY sequence", (job_id,))]

    def close(self):
        self.db.close()
        self._lock.close()
