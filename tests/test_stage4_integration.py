from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CallbackQueryHandler

from database import Database
from handlers import admin, broadcast, sponsor, user
from handlers.admin import _admin_list_markup
from handlers.sponsor import _sponsor_controls
from keyboards.admin import (
    admin_cancel_menu,
    admin_main_menu,
    admin_management_menu,
    broadcast_confirmation_menu,
    sponsor_admin_menu,
    sponsor_cancel_menu,
)
from keyboards.user import main_menu, market_section_menu, sponsor_verification_keyboard


class FakeQuery:
    def __init__(self, data: str, user_id: int = 70):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class RecordingApplication:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.append((handler, group))


def _update(query):
    return SimpleNamespace(callback_query=query)


def _ignore_user_activity(user_id: int) -> None:
    assert user_id > 0


def _buttons(markup):
    return [
        button
        for row in markup.inline_keyboard
        for button in row
    ]


def test_registered_callback_handlers_cover_keyboard_outputs():
    from bot import register_application_handlers

    application = RecordingApplication()
    register_application_handlers(application)
    callbacks = [
        handler
        for handler, _ in application.handlers
        if isinstance(handler, CallbackQueryHandler)
    ]

    keyboard_data = {
        button.callback_data
        for markup in (
            main_menu(),
            market_section_menu("crypto"),
            sponsor_verification_keyboard([{"channel_id": -1001, "username": "@demo"}]),
            admin_main_menu(),
            admin_management_menu(),
            sponsor_admin_menu(),
            admin_cancel_menu(),
            sponsor_cancel_menu(),
            broadcast_confirmation_menu(),
            _admin_list_markup([{"user_id": 81, "username": "test"}]),
            _sponsor_controls(
                [
                    {
                        "channel_id": -1001,
                        "username": "@demo",
                        "title": "Demo",
                        "is_active": 1,
                    }
                ]
            ),
        )
        for button in _buttons(markup)
        if button.callback_data is not None
    }
    expected = {
        "crypto": user.handle_user_callback,
        "dollar": user.handle_user_callback,
        "gold": user.handle_user_callback,
        "oil": user.handle_user_callback,
        "admin_stats": admin.admin_callback_handler,
        "sponsor_list": sponsor.sponsor_callback_handler,
        "broadcast_confirm": broadcast.broadcast_callback_handler,
    }
    for callback_data in keyboard_data:
        matches = [
            handler
            for handler in callbacks
            if handler.pattern.search(callback_data)
        ]
        assert len(matches) == 1, callback_data
        if callback_data.startswith("admin_"):
            expected_handler = admin.admin_callback_handler
        elif callback_data.startswith("sponsor_"):
            expected_handler = sponsor.sponsor_callback_handler
        elif callback_data.startswith("broadcast_"):
            expected_handler = broadcast.broadcast_callback_handler
        else:
            expected_handler = user.handle_user_callback
        assert matches[0].callback == expected_handler, callback_data
    assert all(
        any(callback_data == item for item in keyboard_data)
        for callback_data in expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["crypto", "dollar", "gold", "oil"])
async def test_direct_market_callbacks_require_membership(monkeypatch, asset):
    query = FakeQuery(asset)
    monkeypatch.setattr(user, "_update_user_activity", _ignore_user_activity)
    monkeypatch.setattr(user, "get_active_sponsors", lambda: [{"channel_id": -1001}])
    monkeypatch.setattr(user, "verify_membership", AsyncMock(return_value=False))
    providers = [
        "fetch_crypto_prices",
        "get_dollar_data",
        "get_gold_data",
        "get_oil_data",
    ]
    mocks = {}
    for name in providers:
        mock = AsyncMock()
        mocks[name] = mock
        monkeypatch.setattr(user, name, mock)

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))

    assert any("عضو" in edit[0] for edit in query.edits)
    assert all(not mock.await_count for mock in mocks.values())


@pytest.mark.asyncio
async def test_membership_retry_can_recover_and_open_main_menu(monkeypatch):
    query = FakeQuery("check_membership")
    monkeypatch.setattr(user, "_update_user_activity", _ignore_user_activity)
    monkeypatch.setattr(user, "get_active_sponsors", lambda: [{"channel_id": -1001}])
    verify = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(user, "verify_membership", verify)

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))
    assert "عضو نشده" in query.edits[-1][0]

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))
    assert "تأیید شد" in query.edits[-1][0]
    query.message.reply_text.assert_awaited_once()
    assert verify.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callback_data", "provider", "payload", "expected_fragments"),
    [
        (
            "crypto",
            "fetch_crypto_prices",
            {
                "BTC": {
                    "price": 123.45,
                    "change_24h": -1.2,
                    "updated_at": "fresh-time",
                },
                "_meta": {"is_partial": False, "is_stale": False},
            },
            ["BTC", "۴۵", "fresh-time"],
        ),
        (
            "dollar",
            "get_dollar_data",
            {
                "buy_toman": 50_000,
                "sell_toman": 51_000,
                "updated_at": "fresh-time",
                "is_stale": False,
            },
            ["50,000", "51,000", "fresh-time"],
        ),
        (
            "gold",
            "get_gold_data",
            {
                "price_toman": 250_000,
                "change": None,
                "updated_at": "fresh-time",
                "is_stale": False,
            },
            ["250,000", "fresh-time", "در دسترس نیست"],
        ),
        (
            "oil",
            "get_oil_data",
            {
                "WTI/USD": 70.5,
                "BRENT/USD": 80.25,
                "updated_at": "fresh-time",
                "is_stale": False,
                "_meta": {"is_partial": False, "stale_symbols": []},
            },
            ["۷۰.۵۰", "۸۰.۲۵", "fresh-time"],
        ),
    ],
)
async def test_market_handlers_render_each_fresh_service_contract(
    monkeypatch, callback_data, provider, payload, expected_fragments
):
    query = FakeQuery(callback_data)
    monkeypatch.setattr(user, "_update_user_activity", _ignore_user_activity)
    monkeypatch.setattr(user, "_membership_access", AsyncMock(return_value=(True, [])))
    monkeypatch.setattr(
        user,
        "SETTINGS",
        SimpleNamespace(oil_wti_symbol="WTI/USD", oil_brent_symbol="BRENT/USD"),
    )
    service = AsyncMock(return_value=payload)
    monkeypatch.setattr(user, provider, service)

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))

    rendered = query.edits[-1][0]
    assert all(fragment in rendered for fragment in expected_fragments)
    service.assert_awaited_once_with(force_refresh=False)


@pytest.mark.asyncio
async def test_market_service_failure_is_safe_and_does_not_leak_internal_error(
    monkeypatch, caplog
):
    secret_detail = "private upstream response body"
    query = FakeQuery("gold")
    monkeypatch.setattr(user, "_update_user_activity", _ignore_user_activity)
    monkeypatch.setattr(user, "_membership_access", AsyncMock(return_value=(True, [])))
    monkeypatch.setattr(
        user,
        "get_gold_data",
        AsyncMock(side_effect=RuntimeError(secret_detail)),
    )

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))

    assert "دریافت قیمت ممکن نشد" in query.edits[-1][0]
    assert secret_detail not in query.edits[-1][0]
    assert secret_detail not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset", "provider"),
    [
        ("crypto", "fetch_crypto_prices"),
        ("dollar", "get_dollar_data"),
        ("gold", "get_gold_data"),
        ("oil", "get_oil_data"),
    ],
)
async def test_each_market_provider_failure_isolated_at_user_boundary(
    monkeypatch, caplog, asset, provider
):
    query = FakeQuery(asset)
    secret_detail = "mock upstream payload"
    monkeypatch.setattr(user, "_update_user_activity", _ignore_user_activity)
    monkeypatch.setattr(user, "_membership_access", AsyncMock(return_value=(True, [])))
    service = AsyncMock(side_effect=RuntimeError(secret_detail))
    monkeypatch.setattr(user, provider, service)

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))

    assert "دریافت قیمت ممکن نشد" in query.edits[-1][0]
    assert secret_detail not in query.edits[-1][0]
    assert secret_detail not in caplog.text
    assert service.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset", "provider", "payload"),
    [
        (
            "crypto",
            "fetch_crypto_prices",
            {
                "BTC": {
                    "price": 10,
                    "change_24h": 0,
                    "updated_at": "previous",
                    "is_stale": True,
                    "stale_age_seconds": 120,
                },
                "_meta": {"is_stale": True, "stale_age_seconds": 120},
            },
        ),
        (
            "dollar",
            "get_dollar_data",
            {
                "buy_toman": 10,
                "sell_toman": 11,
                "updated_at": "previous",
                "is_stale": True,
                "stale_age_seconds": 120,
            },
        ),
        (
            "gold",
            "get_gold_data",
            {
                "price_toman": 12,
                "change": None,
                "updated_at": "previous",
                "is_stale": True,
                "stale_age_seconds": 120,
            },
        ),
        (
            "oil",
            "get_oil_data",
            {
                "WTI/USD": 10,
                "BRENT/USD": 11,
                "updated_at": "previous",
                "is_stale": True,
                "stale_age_seconds": 120,
                "_meta": {
                    "is_stale": True,
                    "stale_age_seconds": 120,
                    "stale_symbols": ["WTI/USD", "BRENT/USD"],
                    "quote_updated_at": {
                        "WTI/USD": "old-wti",
                        "BRENT/USD": "old-brent",
                    },
                    "quote_stale_age_seconds": {
                        "WTI/USD": 120,
                        "BRENT/USD": 150,
                    },
                },
            },
        ),
    ],
)
async def test_stale_market_quotes_are_labeled_as_old(
    monkeypatch, asset, provider, payload
):
    query = FakeQuery(asset)
    monkeypatch.setattr(user, "_update_user_activity", _ignore_user_activity)
    monkeypatch.setattr(user, "_membership_access", AsyncMock(return_value=(True, [])))
    monkeypatch.setattr(
        user,
        "SETTINGS",
        SimpleNamespace(oil_wti_symbol="WTI/USD", oil_brent_symbol="BRENT/USD"),
    )
    monkeypatch.setattr(user, provider, AsyncMock(return_value=payload))

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))

    rendered = query.edits[-1][0]
    assert "دادهٔ قدیمی" in rendered
    assert "previous" in rendered
    assert "120 ثانیه" in rendered
    if asset == "oil":
        assert "old-wti" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset", "provider"),
    [
        ("crypto", "fetch_crypto_prices"),
        ("dollar", "get_dollar_data"),
        ("gold", "get_gold_data"),
        ("oil", "get_oil_data"),
    ],
)
async def test_each_market_refresh_callback_forces_refresh(monkeypatch, asset, provider):
    query = FakeQuery(f"refresh_{asset}")
    monkeypatch.setattr(user, "_update_user_activity", _ignore_user_activity)
    monkeypatch.setattr(user, "_membership_access", AsyncMock(return_value=(True, [])))
    monkeypatch.setattr(
        user,
        "SETTINGS",
        SimpleNamespace(oil_wti_symbol="WTI/USD", oil_brent_symbol="BRENT/USD"),
    )
    payloads = {
        "crypto": {"_meta": {}},
        "dollar": {
            "buy_toman": 1,
            "sell_toman": 2,
            "updated_at": "now",
        },
        "gold": {"price_toman": 1, "change": None, "updated_at": "now"},
        "oil": {"updated_at": "now", "_meta": {}},
    }
    service = AsyncMock(return_value=payloads[asset])
    monkeypatch.setattr(user, provider, service)

    await user.handle_user_callback(_update(query), SimpleNamespace(bot=object()))

    service.assert_awaited_once_with(force_refresh=True)
    assert "در حال بروزرسانی" in query.edits[0][0]


def _synchronize_connections(monkeypatch, db, workers=2):
    original_connection = db.connection
    barrier = Barrier(workers)

    @contextmanager
    def synchronized_connection():
        barrier.wait(timeout=5)
        with original_connection() as connection:
            yield connection

    monkeypatch.setattr(db, "connection", synchronized_connection)
    return lambda: monkeypatch.setattr(db, "connection", original_connection)


def test_concurrent_new_user_upserts_are_atomic_and_unique(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "race-users.db"))
    restore_connection = _synchronize_connections(monkeypatch, db)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda name: db.upsert_user(901, username=name, is_active=True),
                ("first", "second"),
            )
        )

    restore_connection()
    assert results == [None, None]
    assert len(db.fetch_all("SELECT * FROM users WHERE user_id = ?", (901,))) == 1


def test_concurrent_sponsor_additions_do_not_create_duplicate_channels(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "race-sponsors.db"))
    restore_connection = _synchronize_connections(monkeypatch, db)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda title: db.add_sponsor(-1009, "@demo", title),
                ("First", "Second"),
            )
        )

    restore_connection()
    rows = db.fetch_all("SELECT * FROM sponsors WHERE channel_id = ?", (-1009,))
    assert len(rows) == 1


def test_concurrent_error_recording_preserves_deduplication(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "race-errors.db"))
    restore_connection = _synchronize_connections(monkeypatch, db)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                db.record_error,
                "RuntimeError",
                "same",
                "trace",
                91,
            )
            for _ in range(2)
        ]
        [future.result() for future in futures]

    restore_connection()
    rows = db.fetch_all("SELECT * FROM errors WHERE error_type = ?", ("RuntimeError",))
    assert len(rows) == 1
    assert rows[0]["count"] == 2


def test_database_migration_is_repeatable_and_preserves_existing_records(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "repeat-migration.db"
    db = Database(str(database_path))
    db.upsert_user(1001, username="saved-user")
    db.add_admin(1002, "saved-admin")
    db.add_sponsor(-1010, "@saved", "Saved channel")
    db.set_setting("sample", "kept")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE legacy_admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                role TEXT NOT NULL DEFAULT 'ADMIN',
                added_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO legacy_admins (user_id, username, role, added_at) VALUES (?, ?, ?, ?)",
            (1002, "saved-admin", "ADMIN", "2024-01-02"),
        )
        connection.execute(
            "INSERT INTO legacy_admins (user_id, username, role, added_at) VALUES (?, ?, ?, ?)",
            (1003, "legacy", "OWNER", "2024-01-01"),
        )
        connection.execute("DROP TABLE admins")
        connection.execute("ALTER TABLE legacy_admins RENAME TO admins")

    monkeypatch.setattr(
        "database.SETTINGS",
        SimpleNamespace(owner_id=None, database_path=str(database_path)),
    )
    migrated = Database(str(database_path))
    migrated.init_db()
    migrated.init_db()

    assert migrated.get_user(1001)["username"] == "saved-user"
    assert migrated.get_admin_role(1002) == "ADMIN"
    assert migrated.get_admin_role(1003) == "ADMIN"
    assert migrated.get_sponsor(-1010)["username"] == "@saved"
    assert migrated.get_setting("sample") == "kept"
    assert len(migrated.list_admins()) == 2


@pytest.mark.asyncio
async def test_legacy_owner_row_never_authorizes_owner_only_callback(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy-permission.db"
    with sqlite3.connect(path) as connection:
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
        connection.execute(
            "INSERT INTO admins (user_id, username, role, added_at) VALUES (?, ?, ?, ?)",
            (1003, "legacy-owner", "OWNER", "2024-01-01"),
        )
    monkeypatch.setattr("database.SETTINGS", SimpleNamespace(owner_id=None))
    db = Database(str(path))
    monkeypatch.setattr(admin, "SETTINGS", SimpleNamespace(owner_id=None))
    monkeypatch.setattr(admin, "get_db", lambda: db)
    query = FakeQuery("admin_admins", user_id=1003)

    await admin.admin_callback_handler(_update(query), SimpleNamespace(user_data={}))

    assert db.get_admin_role(1003) == "ADMIN"
    assert admin.is_admin_authorized(1003)
    assert not admin.is_owner_authorized(1003)
    assert "فقط مالک" in query.edits[-1][0]
