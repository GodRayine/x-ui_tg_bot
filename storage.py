import sqlite3
from typing import List


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init(self):
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY,
                    first_seen_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            c.commit()

    def upsert_user(self, tg_id: int):
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users (tg_id) VALUES (?)",
                (tg_id,),
            )
            c.commit()

    def list_users(self) -> List[int]:
        with self._conn() as c:
            rows = c.execute("SELECT tg_id FROM users").fetchall()
            return [int(r[0]) for r in rows]

    def count_users(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) FROM users").fetchone()
            return int(row[0]) if row else 0