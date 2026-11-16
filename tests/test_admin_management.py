from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus
from telegram.error import RetryAfter, TelegramError

import database as database_module
from database import Database
from handlers import admin, admin_input, broadcast, sponsor
from keyboards.admin import (
    admin_cancel_menu,
    admin_main_menu,
    admin_management_menu,
    broadcast_confirmation_menu,
    sponsor_admin_menu,
    sponsor_cancel_menu,
)
from keyboards.user import main_menu, market_section_menu, sponsor_verification_keyboard


class FakeMessage:
    def __init__(self, text: str | None = None, user_id: int = 777):
        self.text = text
        self.chat_id = user_id
        self.message_id = 40
        self.from_user = SimpleNamespace(id=user_id)
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data: str, user_id: int = 777):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


def callback_update(query):
    return SimpleNamespace(callback_query=query)


def message_update(message):
    return SimpleNamespace(effective_message=message, effective_user=message.from_user)


def configure_admin_db(monkeypatch, db: Database, owner_id: int = 777):
    settings = SimpleNamespace(owner_id=owner_id)
    monkeypatch.setattr(admin, "SETTINGS", settings)
    monkeypatch.setattr(admin, "get_db", lambda: db)
    monkeypatch.setattr(admin_input, "get_db", lambda: db)
    monkeypatch.setattr(sponsor, "get_db", lambda: db)
    monkeypatch.setattr(broadcast, "get_db", lambda: db)
    monkeypatch.setattr(database_module, "SETTINGS", settings)
    db.add_admin(42, "operator")
    return settings


@pytest.mark.asyncio
async def test_owner_can_add_list_and_remove_admins(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "admins.db"))
    configure_admin_db(monkeypatch, db)
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    add_query = FakeQuery("admin_add")
    await admin.admin_callback_handler(callback_update(add_query), context)
    assert context.user_data[admin.ADMIN_INPUT_KEY] == "admin_add"

    new_admin = FakeMessage("123456")
    await admin_input.admin_input_handler(message_update(new_admin), context)
    assert db.get_admin_role(123456) == "ADMIN"
    assert any("افزوده شد" in text for text, _ in new_admin.replies)

    list_query = FakeQuery("admin_list")
    await admin.admin_callback_handler(callback_update(list_query), context)
    assert "123456" in list_query.edits[0][0]
    remove_button_data = next(
        button.callback_data
        for row in list_query.edits[0][1].inline_keyboard
        for button in row
        if button.callback_data == "admin_remove:123456"
    )

    remove_query = FakeQuery(remove_button_data)
    await admin.admin_callback_handler(callback_update(remove_query), context)
    assert db.get_admin_role(123456) is None
    assert "حذف شد" in remove_query.edits[0][0]


@pytest.mark.asyncio
async def test_admin_add_validates_ids_duplicates_and_database_failures(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "admins.db"))
    configure_admin_db(monkeypatch, db)
    context = SimpleNamespace(user_data={admin.ADMIN_INPUT_KEY: "admin_add"}, bot=None)

    invalid = FakeMessage("-4")
    await admin_input.admin_input_handler(message_update(invalid), context)
    assert context.user_data[admin.ADMIN_INPUT_KEY] == "admin_add"
    assert "عدد مثبت" in invalid.replies[0][0]

    duplicate = FakeMessage("42")
    await admin_input.admin_input_handler(message_update(duplicate), context)
    assert "از قبل ادمین" in duplicate.replies[0][0]
    assert len(db.list_admins()) == 1

    context.user_data[admin.ADMIN_INPUT_KEY] = "admin_add"
    monkeypatch.setattr(
        admin_input,
        "get_db",
        lambda: SimpleNamespace(
            get_admin_role=lambda user_id: None,
            add_admin=lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError("offline")),
        ),
    )
    failure = FakeMessage("987")
    await admin_input.admin_input_handler(message_update(failure), context)
    assert "پایگاه داده ناموفق" in failure.replies[0][0]


@pytest.mark.asyncio
async def test_admin_operations_are_owner_only_and_owner_cannot_be_removed(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "admins.db"))
    configure_admin_db(monkeypatch, db)
    db.execute(
        "INSERT INTO admins (user_id, username, role, added_at) VALUES (?, ?, ?, ?)",
        (777, "legacy-owner-row", "ADMIN", "2024-01-01"),
    )
    context = SimpleNamespace(user_data={}, bot=None)

    denied = FakeQuery("admin_add", user_id=42)
    await admin.admin_callback_handler(callback_update(denied), context)
    assert "فقط مالک" in denied.edits[0][0]
    assert not context.user_data

    assert db.remove_admin(777) is False
    owner_delete = FakeQuery("admin_remove:777")
    await admin.admin_callback_handler(callback_update(owner_delete), context)
    assert "مالک قابل حذف نیست" in owner_delete.edits[0][0]
    assert db.get_admin_role(777) is None


@pytest.mark.asyncio
async def test_admin_remove_unknown_and_cancel_are_reported(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "admins.db"))
    configure_admin_db(monkeypatch, db)
    context = SimpleNamespace(user_data={admin.ADMIN_INPUT_KEY: "admin_remove"}, bot=None)

    missing = FakeMessage("555")
    await admin_input.admin_input_handler(message_update(missing), context)
    assert "پیدا نشد" in missing.replies[0][0]
    assert admin.ADMIN_INPUT_KEY not in context.user_data

    context.user_data[admin.ADMIN_INPUT_KEY] = "admin_add"
    cancelled = FakeQuery("admin_cancel")
    await admin.admin_callback_handler(callback_update(cancelled), context)
    assert admin.ADMIN_INPUT_KEY not in context.user_data
    assert "لغو" in cancelled.edits[0][0]


@pytest.mark.asyncio
async def test_sponsor_add_uses_telegram_resolution_and_persists_database_row(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "sponsors.db"))
    configure_admin_db(monkeypatch, db)
    bot = SimpleNamespace(
        id=9999,
        get_chat=AsyncMock(
            return_value=SimpleNamespace(
                id=-100222,
                username="testchannel",
                title="Test Channel",
                type="channel",
            )
        ),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)
        ),
    )
    context = SimpleNamespace(user_data={}, bot=bot)
    query = FakeQuery("sponsor_add", user_id=42)

    await sponsor.sponsor_callback_handler(callback_update(query), context)
    assert context.user_data[admin.ADMIN_INPUT_KEY] == "sponsor_add_waiting"
    message = FakeMessage("@testchannel", user_id=42)
    await admin_input.admin_input_handler(message_update(message), context)

    saved = db.get_sponsor(-100222)
    assert saved is not None
    assert saved["username"] == "@testchannel"
    assert saved["is_active"] == 1
    assert "در پایگاه داده ثبت شد" in message.replies[0][0]
    bot.get_chat.assert_awaited_once_with("@testchannel")
    bot.get_chat_member.assert_awaited_once_with(chat_id=-100222, user_id=9999)


@pytest.mark.asyncio
async def test_sponsor_is_not_saved_when_bot_lacks_admin_rights(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "sponsors.db"))
    configure_admin_db(monkeypatch, db)
    bot = SimpleNamespace(
        id=9999,
        get_chat=AsyncMock(
            return_value=SimpleNamespace(
                id=-100222,
                username="testchannel",
                title="Test Channel",
                type="channel",
            )
        ),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER)
        ),
    )
    context = SimpleNamespace(
        user_data={admin.ADMIN_INPUT_KEY: "sponsor_add_waiting"},
        bot=bot,
    )
    message = FakeMessage("@testchannel", user_id=42)

    await admin_input.admin_input_handler(message_update(message), context)

    assert db.get_sponsor(-100222) is None
    assert "افزودن اسپانسر انجام نشد" in message.replies[0][0]


@pytest.mark.asyncio
async def test_sponsor_invalid_input_and_api_failure_do_not_claim_success(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "sponsors.db"))
    configure_admin_db(monkeypatch, db)
    bot = SimpleNamespace(get_chat=AsyncMock(side_effect=TelegramError("not accessible")))
    context = SimpleNamespace(user_data={admin.ADMIN_INPUT_KEY: "sponsor_add_waiting"}, bot=bot)

    invalid = FakeMessage("not a channel", user_id=42)
    await admin_input.admin_input_handler(message_update(invalid), context)
    assert "ورودی نامعتبر" in invalid.replies[0][0]
    assert db.list_sponsors() == []

    context.user_data[admin.ADMIN_INPUT_KEY] = "sponsor_add_waiting"
    unavailable = FakeMessage("@testchannel", user_id=42)
    await admin_input.admin_input_handler(message_update(unavailable), context)
    assert "افزودن اسپانسر انجام نشد" in unavailable.replies[0][0]
    assert db.list_sponsors() == []


@pytest.mark.asyncio
async def test_sponsor_toggle_and_delete_are_verified_against_database(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "sponsors.db"))
    configure_admin_db(monkeypatch, db)
    db.add_sponsor(-100333, "@channel", "Channel", is_active=True)
    context = SimpleNamespace(user_data={}, bot=None)

    toggle = FakeQuery("sponsor_toggle:-100333", user_id=42)
    await sponsor.sponsor_callback_handler(callback_update(toggle), context)
    assert db.get_sponsor(-100333)["is_active"] == 0
    assert "غیرفعال" in toggle.edits[-1][0]

    delete = FakeQuery("sponsor_delete:-100333", user_id=42)
    await sponsor.sponsor_callback_handler(callback_update(delete), context)
    assert db.get_sponsor(-100333) is None
    assert "حذف شد" in delete.edits[-1][0]


@pytest.mark.asyncio
async def test_sponsor_callback_denies_non_admin_and_reports_database_failure(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "sponsors.db"))
    configure_admin_db(monkeypatch, db)

    denied = FakeQuery("sponsor_list", user_id=999)
    await sponsor.sponsor_callback_handler(
        callback_update(denied),
        SimpleNamespace(user_data={}),
    )
    assert denied.answers[0][1].get("show_alert") is True

    monkeypatch.setattr(
        sponsor,
        "get_db",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("offline")),
    )
    failed = FakeQuery("sponsor_list", user_id=42)
    await sponsor.sponsor_callback_handler(
        callback_update(failed),
        SimpleNamespace(user_data={}),
    )
    assert "ناموفق بود" in failed.edits[-1][0]


@pytest.mark.asyncio
async def test_broadcast_preview_cancel_and_mocked_delivery(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "broadcast.db"))
    configure_admin_db(monkeypatch, db)
    db.upsert_user(101, first_name="One")
    db.upsert_user(102, first_name="Two")
    context = SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(copy_message=AsyncMock(side_effect=[None, TelegramError("blocked")])),
    )
    start_query = FakeQuery("broadcast_start", user_id=42)
    await broadcast.broadcast_callback_handler(callback_update(start_query), context)
    assert context.user_data[admin.ADMIN_INPUT_KEY] == "broadcast_waiting"

    source = FakeMessage("Announcement", user_id=42)
    await admin_input.admin_input_handler(message_update(source), context)
    assert context.user_data[broadcast.BROADCAST_DRAFT_KEY] == {
        "chat_id": 42,
        "message_id": 40,
    }
    assert "پیش‌نمایش" in source.replies[0][0]

    confirm = FakeQuery("broadcast_confirm", user_id=42)
    await broadcast.broadcast_callback_handler(callback_update(confirm), context)
    assert context.bot.copy_message.await_count == 2
    assert "موفق: 1" in confirm.edits[-1][0]
    assert "ناموفق: 1" in confirm.edits[-1][0]
    assert broadcast.BROADCAST_DRAFT_KEY not in context.user_data

    context.user_data[broadcast.BROADCAST_DRAFT_KEY] = {"chat_id": 42, "message_id": 40}
    cancel = FakeQuery("broadcast_cancel", user_id=42)
    await broadcast.broadcast_callback_handler(callback_update(cancel), context)
    assert broadcast.BROADCAST_DRAFT_KEY not in context.user_data
    assert "لغو" in cancel.edits[0][0]


@pytest.mark.asyncio
async def test_broadcast_requires_confirmation_and_admin_authorization(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "broadcast.db"))
    configure_admin_db(monkeypatch, db)
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace(copy_message=AsyncMock()))

    unauthorized = FakeQuery("broadcast_confirm", user_id=999)
    await broadcast.broadcast_callback_handler(callback_update(unauthorized), context)
    assert unauthorized.answers[0][1].get("show_alert") is True
    assert context.bot.copy_message.await_count == 0

    no_preview = FakeQuery("broadcast_confirm", user_id=42)
    await broadcast.broadcast_callback_handler(callback_update(no_preview), context)
    assert "منقضی" in no_preview.edits[-1][0]
    assert context.bot.copy_message.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_sender_is_rate_limited_and_continues_after_failure():
    users = [{"user_id": 1}, {"user_id": 2}, {"user_id": 3}]
    bot = SimpleNamespace(
        copy_message=AsyncMock(side_effect=[None, TelegramError("blocked"), None])
    )
    sleeps = []

    async def record_sleep(seconds):
        sleeps.append(seconds)

    sent, failed = await broadcast.send_broadcast(
        bot,
        users,
        777,
        12,
        delay=0.05,
        sleep=record_sleep,
    )

    assert (sent, failed) == (2, 1)
    assert bot.copy_message.await_count == 3
    assert sleeps == [0.05, 0.05, 0.05]


@pytest.mark.asyncio
async def test_broadcast_retries_telegram_rate_limit_before_counting_delivery():
    bot = SimpleNamespace(
        copy_message=AsyncMock(side_effect=[RetryAfter(0.2), None])
    )
    sleeps = []

    async def record_sleep(seconds):
        sleeps.append(seconds)

    sent, failed = await broadcast.send_broadcast(
        bot,
        [{"user_id": 9}],
        777,
        12,
        delay=0,
        sleep=record_sleep,
    )

    assert (sent, failed) == (1, 0)
    assert bot.copy_message.await_count == 2
    assert sleeps == [0.2, 0]


def test_every_keyboard_callback_matches_a_registered_handler():
    from bot import register_application_handlers

    class FakeApplication:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler, group=0):
            self.handlers.append((handler, group))

    application = FakeApplication()
    register_application_handlers(application)
    callbacks = [
        button.callback_data
        for keyboard in (
            admin_main_menu(),
            admin_management_menu(),
            sponsor_admin_menu(),
            admin_cancel_menu(),
            sponsor_cancel_menu(),
            broadcast_confirmation_menu(),
            main_menu(),
            market_section_menu("crypto"),
            sponsor_verification_keyboard(
                [{"channel_id": -100111, "username": "@testchannel"}]
            ),
        )
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]
    callbacks.extend(
        ["admin_remove:42", "sponsor_toggle:-100111", "sponsor_delete:-100111"]
    )

    handlers = [handler for handler, _group in application.handlers]
    for callback_data in callbacks:
        matching = [
            handler
            for handler in handlers
            if hasattr(handler, "pattern")
            and handler.pattern.match(callback_data)
        ]
        assert len(matching) == 1, f"{callback_data} has {len(matching)} registered handlers"
