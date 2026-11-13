"""SQLite persistence for check results."""

from __future__ import annotations

import itertools
import sqlite3
import statistics
import threading
import time

from .checks import CheckResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    target_id  TEXT    NOT NULL,
    ts         REAL    NOT NULL,
    ok         INTEGER NOT NULL,
    latency_ms REAL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_target_ts ON results (target_id, ts);
"""


class Store:
    def __init__(self, path: str = "netpulse.db"):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(self, result: CheckResult) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO results (target_id, ts, ok, latency_ms, detail) VALUES (?, ?, ?, ?, ?)",
                (result.target_id, result.ts, int(result.ok), result.latency_ms, result.detail),
            )
            self._conn.commit()

    def history(self, target_id: str, since: float, limit: int = 5000) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, ok, latency_ms, detail FROM results "
                "WHERE target_id = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
                (target_id, since, limit),
            ).fetchall()
        return [
            {"ts": ts, "ok": bool(ok), "latency_ms": lat, "detail": detail}
            for ts, ok, lat, detail in reversed(rows)
        ]

    def stats(self, target_id: str, since: float) -> dict:
        rows = self.history(target_id, since)
        return summarize(rows)

    def prune(self, older_than: float) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM results WHERE ts < ?", (older_than,))
            self._conn.commit()
            return cur.rowcount


def summarize(rows: list[dict]) -> dict:
    """Uptime, loss, latency percentiles and jitter for a list of results."""
    total = len(rows)
    if total == 0:
        return {"samples": 0, "uptime": None, "loss": None, "avg_ms": None,
                "p95_ms": None, "min_ms": None, "max_ms": None, "jitter_ms": None}

    lat = [r["latency_ms"] for r in rows if r["ok"] and r["latency_ms"] is not None]
    up = sum(1 for r in rows if r["ok"])
    out = {
        "samples": total,
        "uptime": round(up / total * 100, 2),
        "loss": round((total - up) / total * 100, 2),
        "avg_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None, "jitter_ms": None,
    }
    if lat:
        ordered = sorted(lat)
        p95_index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
        out.update(
            avg_ms=round(statistics.fmean(lat), 2),
            p95_ms=round(ordered[p95_index], 2),
            min_ms=round(ordered[0], 2),
            max_ms=round(ordered[-1], 2),
            # RFC 3550-style jitter: mean absolute difference between consecutive samples
            jitter_ms=round(statistics.fmean(abs(a - b) for a, b in itertools.pairwise(lat)), 2)
            if len(lat) > 1 else 0.0,
        )
    return out


def window_start(minutes: float) -> float:
    return time.time() - minutes * 60
