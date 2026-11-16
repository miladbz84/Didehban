from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from database import Database


def test_database_initialization_creates_tables(tmp_path):
    db_path = tmp_path / "dideban.db"
    db = Database(str(db_path))

    tables = db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {row["name"] for row in tables}

    assert {"users", "admins", "sponsors", "errors", "activity_logs", "settings"}.issubset(names)


def test_database_user_creation_and_update(tmp_path):
    db = Database(str(tmp_path / "dideban.db"))
    now = datetime.utcnow().isoformat()

    db.upsert_user(user_id=101, username="ali", first_name="Ali", is_active=True, joined_at=now)
    row = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (101,))
    assert row is not None
    assert row["username"] == "ali"
    assert row["first_name"] == "Ali"

    db.upsert_user(user_id=101, username="ali_2", first_name="Ali New", is_active=False, last_seen=now)
    updated = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (101,))
    assert updated["username"] == "ali_2"
    assert updated["first_name"] == "Ali New"
    assert updated["is_active"] == 0


def test_database_error_deduplication(tmp_path):
    db = Database(str(tmp_path / "dideban.db"))
    now = datetime.utcnow().isoformat()

    db.record_error("ValueError", "bad value", "traceback", 12, now)
    db.record_error("ValueError", "bad value", "traceback", 12, now)
    row = db.fetch_one("SELECT * FROM errors WHERE error_type = ?", ("ValueError",))

    assert row is not None
    assert row["count"] >= 2


def test_database_sponsor_crud(tmp_path):
    db = Database(str(tmp_path / "dideban.db"))
    now = datetime.utcnow().isoformat()

    db.add_sponsor(channel_id=-1001, username="@demo", title="Demo", is_active=True, added_at=now)
    sponsor = db.fetch_one("SELECT * FROM sponsors WHERE channel_id = ?", (-1001,))
    assert sponsor is not None
    assert sponsor["username"] == "@demo"

    db.toggle_sponsor(-1001, False)
    updated = db.fetch_one("SELECT * FROM sponsors WHERE channel_id = ?", (-1001,))
    assert updated["is_active"] == 0


def test_database_migrates_duplicate_sponsors_and_enforces_channel_uniqueness(tmp_path):
    db_path = tmp_path / "legacy-duplicate-sponsors.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE sponsors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                username TEXT,
                title TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO sponsors (id, channel_id, username, title, is_active, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (4, -1001, None, "Old title", 0, "2024-01-01"),
                (7, -1001, "@demo", None, 1, "2024-02-01"),
                (8, -1002, "@other", "Other", 1, "2024-03-01"),
            ],
        )

    db = Database(str(db_path))
    db.init_db()

    sponsors = db.list_sponsors()
    assert [(row["id"], row["channel_id"]) for row in sponsors] == [
        (8, -1002),
        (4, -1001),
    ]
    merged = db.get_sponsor(-1001)
    assert merged["username"] == "@demo"
    assert merged["title"] == "Old title"
    assert merged["is_active"] == 1
    assert merged["added_at"] == "2024-01-01"
    assert db.fetch_one(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_sponsors_channel_id_unique'"
    ) is not None

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO sponsors (channel_id, username, title, is_active, added_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (-1001, "@duplicate", "Duplicate", 1, "2024-04-01"),
        )

    db.init_db()
    assert len(db.fetch_all("SELECT * FROM sponsors WHERE channel_id = ?", (-1001,))) == 1


def test_database_repeated_sponsor_addition_updates_single_channel_record(tmp_path):
    db = Database(str(tmp_path / "repeated-sponsor.db"))

    first_id = db.add_sponsor(-1001, "@first", "First", is_active=False)
    second_id = db.add_sponsor(-1001, "@updated", "Updated", is_active=True)

    assert first_id == second_id
    rows = db.fetch_all("SELECT * FROM sponsors WHERE channel_id = ?", (-1001,))
    assert len(rows) == 1
    assert rows[0]["username"] == "@updated"
    assert rows[0]["title"] == "Updated"
    assert rows[0]["is_active"] == 1


def test_database_rejects_owner_role_and_limits_stored_roles(tmp_path):
    db = Database(str(tmp_path / "dideban.db"))

    with pytest.raises(ValueError):
        db.add_admin(101, "ali", role="OWNER")

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO admins (user_id, username, role, added_at) VALUES (?, ?, ?, ?)",
            (102, "outsider", "OWNER", "2024-01-01T00:00:00"),
        )


def test_database_does_not_store_configured_owner_as_an_admin(tmp_path, monkeypatch):
    monkeypatch.setattr("database.SETTINGS", type("Settings", (), {"owner_id": 501})())
    db = Database(str(tmp_path / "dideban.db"))

    with pytest.raises(ValueError):
        db.add_admin(501, "configured-owner")


def test_database_migrates_legacy_owner_rows_without_losing_admins(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                role TEXT NOT NULL DEFAULT 'ADMIN',
                added_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO admins (user_id, username, role, added_at) VALUES (?, ?, ?, ?)",
            [
                (101, "legacy-owner", "OWNER", "2024-01-01"),
                (102, "admin", "ADMIN", "2024-01-02"),
            ],
        )

    db = Database(str(db_path))

    assert db.get_admin_role(101) == "ADMIN"
    assert db.get_admin_role(102) == "ADMIN"
    assert len(db.list_admins()) == 2
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO admins (user_id, username, role, added_at) VALUES (?, ?, ?, ?)",
            (103, "new-owner", "OWNER", "2024-01-03"),
        )
