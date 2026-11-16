from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config import SETTINGS


class Database:
    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path or SETTINGS.database_path
        self._ensure_parent_directory()
        self.init_db()

    def _ensure_parent_directory(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        schema = [
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                joined_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                role TEXT NOT NULL DEFAULT 'ADMIN'
                    CHECK (role IN ('ADMIN', 'MODERATOR')),
                added_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sponsors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                username TEXT,
                title TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                error_type TEXT,
                message TEXT,
                traceback TEXT,
                user_id INTEGER,
                created_at TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                last_seen TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """,
        ]
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for statement in schema:
                conn.execute(statement)
            admin_schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'admins'"
            ).fetchone()["sql"]
            if "CHECK (role IN ('ADMIN', 'MODERATOR'))" not in admin_schema:
                conn.execute(
                    """
                    CREATE TABLE admins_migrated (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER UNIQUE NOT NULL,
                        username TEXT,
                        role TEXT NOT NULL DEFAULT 'ADMIN'
                            CHECK (role IN ('ADMIN', 'MODERATOR')),
                        added_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO admins_migrated (id, user_id, username, role, added_at)
                    SELECT id, user_id, username,
                        CASE WHEN upper(role) = 'MODERATOR' THEN 'MODERATOR' ELSE 'ADMIN' END,
                        added_at
                    FROM admins
                    """
                )
                conn.execute("DROP TABLE admins")
                conn.execute("ALTER TABLE admins_migrated RENAME TO admins")
            duplicate_channels = conn.execute(
                """
                SELECT channel_id, MIN(id) AS keep_id
                FROM sponsors
                GROUP BY channel_id
                HAVING COUNT(*) > 1
                """
            ).fetchall()
            for duplicate in duplicate_channels:
                rows = conn.execute(
                    "SELECT * FROM sponsors WHERE channel_id = ? ORDER BY id",
                    (duplicate["channel_id"],),
                ).fetchall()
                survivor = rows[0]
                username = next((row["username"] for row in rows if row["username"]), None)
                title = next((row["title"] for row in rows if row["title"]), None)
                # Keep the channel active if any legacy duplicate was active.
                is_active = int(any(row["is_active"] for row in rows))
                conn.execute(
                    """
                    UPDATE sponsors
                    SET username = ?, title = ?, is_active = ?
                    WHERE id = ?
                    """,
                    (username, title, is_active, survivor["id"]),
                )
                conn.executemany(
                    "DELETE FROM sponsors WHERE id = ?",
                    ((row["id"],) for row in rows[1:]),
                )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_sponsors_channel_id_unique
                ON sponsors(channel_id)
                """
            )
            conn.commit()

    def execute(self, query: str, params: Iterable[Any] | None = None) -> list[sqlite3.Row]:
        with self.connection() as conn:
            cursor = conn.execute(query, tuple(params) if params else ())
            conn.commit()
            return cursor.fetchall()

    def fetch_one(self, query: str, params: Iterable[Any] | None = None) -> sqlite3.Row | None:
        with self.connection() as conn:
            cursor = conn.execute(query, tuple(params) if params else ())
            return cursor.fetchone()

    def fetch_all(self, query: str, params: Iterable[Any] | None = None) -> list[sqlite3.Row]:
        with self.connection() as conn:
            cursor = conn.execute(query, tuple(params) if params else ())
            return cursor.fetchall()

    def insert(self, table: str, data: dict[str, Any]) -> int:
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = tuple(data.values())
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        with self.connection() as conn:
            cursor = conn.execute(query, values)
            conn.commit()
            return cursor.lastrowid

    def update(self, table: str, values: dict[str, Any], where: str, where_values: Iterable[Any]) -> None:
        assignments = ", ".join(f"{key} = ?" for key in values)
        query = f"UPDATE {table} SET {assignments} WHERE {where}"
        all_values = tuple(values.values()) + tuple(where_values)
        with self.connection() as conn:
            conn.execute(query, all_values)
            conn.commit()

    def delete(self, table: str, where: str, where_values: Iterable[Any]) -> None:
        query = f"DELETE FROM {table} WHERE {where}"
        with self.connection() as conn:
            conn.execute(query, tuple(where_values))
            conn.commit()

    def upsert_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        is_active: bool | None = None,
        joined_at: str | None = None,
        last_seen: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        active_for_insert = 1 if is_active is not False else 0
        active_for_update = 1 if is_active else 0
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, username, first_name, is_active, joined_at, last_seen
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    first_name = COALESCE(excluded.first_name, users.first_name),
                    is_active = CASE WHEN ? THEN ? ELSE users.is_active END,
                    last_seen = excluded.last_seen
                """,
                (
                    user_id,
                    username,
                    first_name,
                    active_for_insert,
                    joined_at or now,
                    last_seen or now,
                    is_active is not None,
                    active_for_update,
                ),
            )
            conn.commit()

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        return self.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    def list_users(self, active_only: bool | None = None) -> list[sqlite3.Row]:
        if active_only is None:
            return self.fetch_all("SELECT * FROM users ORDER BY last_seen DESC")
        return self.fetch_all(
            "SELECT * FROM users WHERE is_active = ? ORDER BY last_seen DESC",
            (1 if active_only else 0,),
        )

    def record_error(
        self,
        error_type: str,
        message: str,
        traceback_text: str,
        user_id: int | None = None,
        created_at: str | None = None,
    ) -> sqlite3.Row | None:
        now = created_at or datetime.utcnow().isoformat()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM errors
                WHERE error_type = ? AND message = ? AND traceback = ? AND user_id IS ?
                ORDER BY id
                LIMIT 1
                """,
                (error_type, message, traceback_text, user_id),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE errors SET count = count + 1, last_seen = ? WHERE id = ?",
                    (now, existing["id"]),
                )
                error_id = existing["id"]
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO errors (
                        error_type, message, traceback, user_id, created_at, count, last_seen
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (error_type, message, traceback_text, user_id, now, now),
                )
                error_id = cursor.lastrowid
            row = conn.execute(
                "SELECT * FROM errors WHERE id = ?",
                (error_id,),
            ).fetchone()
            conn.commit()
            return row

    def get_recent_errors(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.fetch_all(
            "SELECT * FROM errors ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        )

    def get_error_by_id(self, error_id: int) -> sqlite3.Row | None:
        return self.fetch_one("SELECT * FROM errors WHERE id = ?", (error_id,))

    def delete_errors(self) -> None:
        self.execute("DELETE FROM errors")

    def log_activity(self, admin_id: int | None, action: str, details: str | None = None) -> None:
        self.insert(
            "activity_logs",
            {
                "admin_id": admin_id,
                "action": action,
                "details": details,
                "created_at": datetime.utcnow().isoformat(),
            },
        )

    def get_activity_logs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.fetch_all(
            "SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    def get_admin_role(self, user_id: int) -> str | None:
        if SETTINGS.owner_id is not None and user_id == SETTINGS.owner_id:
            return None
        row = self.fetch_one("SELECT role FROM admins WHERE user_id = ?", (user_id,))
        if row is None:
            return None
        role = str(row["role"]).upper() if row["role"] else None
        return role if role in {"ADMIN", "MODERATOR"} else None

    def add_admin(self, user_id: int, username: str | None, role: str = "ADMIN") -> None:
        if not isinstance(role, str):
            raise ValueError("Stored administrator role must be ADMIN or MODERATOR.")
        normalized_role = role.upper()
        if normalized_role not in {"ADMIN", "MODERATOR"}:
            raise ValueError("Stored administrator role must be ADMIN or MODERATOR.")
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or not 0 < user_id <= 2**63 - 1
        ):
            raise ValueError("Administrator user id must be a positive integer.")
        if SETTINGS.owner_id is not None and user_id == SETTINGS.owner_id:
            raise ValueError("The configured owner cannot be stored as an administrator.")
        self.insert(
            "admins",
            {
                "user_id": user_id,
                "username": username,
                "role": normalized_role,
                "added_at": datetime.utcnow().isoformat(),
            },
        )

    def remove_admin(self, user_id: int) -> bool:
        if SETTINGS.owner_id is not None and user_id == SETTINGS.owner_id:
            return False
        with self.connection() as conn:
            cursor = conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0

    def list_admins(self) -> list[sqlite3.Row]:
        if SETTINGS.owner_id is None:
            return self.fetch_all("SELECT * FROM admins ORDER BY added_at DESC")
        return self.fetch_all(
            "SELECT * FROM admins WHERE user_id != ? ORDER BY added_at DESC",
            (SETTINGS.owner_id,),
        )

    def add_sponsor(
        self,
        channel_id: int,
        username: str | None,
        title: str | None,
        is_active: bool = True,
        added_at: str | None = None,
    ) -> int:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM sponsors WHERE channel_id = ? ORDER BY id LIMIT 1",
                (channel_id,),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE sponsors
                    SET username = ?, title = ?, is_active = ?, added_at = ?
                    WHERE channel_id = ?
                    """,
                    (
                        username,
                        title,
                        1 if is_active else 0,
                        added_at or existing["added_at"],
                        channel_id,
                    ),
                )
                sponsor_id = int(existing["id"])
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO sponsors (channel_id, username, title, is_active, added_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        channel_id,
                        username,
                        title,
                        1 if is_active else 0,
                        added_at or datetime.utcnow().isoformat(),
                    ),
                )
                sponsor_id = cursor.lastrowid
            conn.commit()
            return sponsor_id

    def get_sponsor(self, channel_id: int) -> sqlite3.Row | None:
        return self.fetch_one("SELECT * FROM sponsors WHERE channel_id = ?", (channel_id,))

    def toggle_sponsor(self, channel_id: int, is_active: bool | None = None) -> bool:
        sponsor = self.get_sponsor(channel_id)
        if sponsor is None:
            return False
        new_value = (0 if sponsor["is_active"] else 1) if is_active is None else (1 if is_active else 0)
        self.update("sponsors", {"is_active": new_value}, "channel_id = ?", (channel_id,))
        updated = self.get_sponsor(channel_id)
        return updated is not None and bool(updated["is_active"]) == bool(new_value)

    def delete_sponsor(self, channel_id: int) -> bool:
        with self.connection() as conn:
            cursor = conn.execute("DELETE FROM sponsors WHERE channel_id = ?", (channel_id,))
            conn.commit()
            return cursor.rowcount > 0

    def list_sponsors(self, active_only: bool | None = None) -> list[sqlite3.Row]:
        if active_only is None:
            return self.fetch_all("SELECT * FROM sponsors ORDER BY added_at DESC")
        return self.fetch_all(
            "SELECT * FROM sponsors WHERE is_active = ? ORDER BY added_at DESC",
            (1 if active_only else 0,),
        )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        return default if row is None else row["value"]

    def set_setting(self, key: str, value: str) -> None:
        existing = self.fetch_one("SELECT 1 FROM settings WHERE key = ?", (key,))
        if existing is None:
            self.insert("settings", {"key": key, "value": str(value)})
        else:
            self.update("settings", {"value": str(value)}, "key = ?", (key,))

    def get_settings_map(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.fetch_all("SELECT * FROM settings")}


def get_db() -> Database:
    return Database()
