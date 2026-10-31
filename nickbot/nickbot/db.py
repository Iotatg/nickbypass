"""SQLite 存储：用户、解析记录、封禁、统计。没有 ORM，够用就行。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    requests    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS requests (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    domain    TEXT,
    url       TEXT,
    final_url TEXT,
    ok        INTEGER NOT NULL,
    method    TEXT,
    elapsed   REAL,
    ts        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_req_ts ON requests(ts);
CREATE TABLE IF NOT EXISTS bans (
    user_id INTEGER PRIMARY KEY,
    reason  TEXT,
    ts      INTEGER NOT NULL
);
"""


class DB:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------------- users

    def touch_user(self, user_id: int, username: str = "", first_name: str = "") -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """INSERT INTO users (id, username, first_name, first_seen, last_seen, requests)
                   VALUES (?, ?, ?, ?, ?, 0)
                   ON CONFLICT(id) DO UPDATE SET
                     username=excluded.username,
                     first_name=excluded.first_name,
                     last_seen=excluded.last_seen""",
                (user_id, username, first_name, now, now),
            )
            self._conn.commit()

    def bump_user(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET requests = requests + 1 WHERE id = ?", (user_id,))
            self._conn.commit()

    def user_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    # ------------------------------------------------------------- requests

    def log_request(
        self, user_id: int, domain: str, url: str, final_url: str,
        ok: bool, method: str, elapsed: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO requests (user_id, domain, url, final_url, ok, method, elapsed, ts)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user_id, domain, url, final_url, int(ok), method, elapsed, int(time.time())),
            )
            self._conn.commit()

    def stats(self) -> Dict[str, Any]:
        now = int(time.time())
        day = now - 86400
        with self._lock:
            c = self._conn
            total = c.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            today = c.execute("SELECT COUNT(*) FROM requests WHERE ts >= ?", (day,)).fetchone()[0]
            ok = c.execute("SELECT COUNT(*) FROM requests WHERE ok = 1 AND ts >= ?", (day,)).fetchone()[0]
            users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active = c.execute("SELECT COUNT(DISTINCT user_id) FROM requests WHERE ts >= ?", (day,)).fetchone()[0]
            top = c.execute(
                "SELECT domain, COUNT(*) n FROM requests WHERE ts >= ? GROUP BY domain ORDER BY n DESC LIMIT 5",
                (day,),
            ).fetchall()
            avg = c.execute("SELECT AVG(elapsed) FROM requests WHERE ts >= ?", (day,)).fetchone()[0]
        return {
            "total": total, "today": today, "ok_today": ok, "users": users,
            "active_today": active, "avg_elapsed": avg or 0.0,
            "top": [(r["domain"], r["n"]) for r in top],
            "success_rate": (ok / today * 100) if today else 0.0,
        }

    # ----------------------------------------------------------------- bans

    def ban(self, user_id: int, reason: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO bans (user_id, reason, ts) VALUES (?,?,?)",
                (user_id, reason, int(time.time())),
            )
            self._conn.commit()

    def unban(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
            self._conn.commit()

    def is_banned(self, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM bans WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

    def all_user_ids(self) -> List[int]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM users").fetchall()
        return [r["id"] for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
