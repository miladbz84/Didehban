from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus

from database import Database
from handlers import admin, broadcast, sponsor, start, user
from services.sponsor import MembershipCheckError, verify_membership


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data: str, user_id: int):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage()
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


def _callback_update(query: FakeQuery):
    return SimpleNamespace(callback_query=query)


@pytest.mark.asyncio
async def test_verify_membership_accepts_member_and_rejects_nonmember():
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status=ChatMemberStatus.MEMBER),
                SimpleNamespace(status=ChatMemberStatus.LEFT),
            ]
        )
    )
    sponsor_row = {"channel_id": -100123}

    assert await verify_membership(bot, 17, sponsor_row) is True
    assert await verify_membership(bot, 17, sponsor_row) is False
    assert bot.get_chat_member.await_count == 2


@pytest.mark.asyncio
async def test_verify_membership_api_error_is_not_treated_as_nonmember_result():
    bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=RuntimeError("API unavailable")))

    with pytest.raises(MembershipCheckError):
        await verify_membership(bot, 17, {"channel_id": -100123})


@pytest.mark.asyncio
async def test_start_awaits_membership_checks_and_uses_context_bot(monkeypatch):
    db = SimpleNamespace(upsert_user=lambda **kwargs: None)
    sponsor_row = {"channel_id": -100123, "username": "@demo", "title": "Demo"}
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(start, "get_db", lambda: db)
    monkeypatch.setattr(start, "get_active_sponsors", lambda: [sponsor_row])
    monkeypatch.setattr(start, "verify_membership", verify)

    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=17, username="tester", first_name="Test"),
        effective_chat=SimpleNamespace(id=17),
        message=message,
    )
    bot = object()

    await start.start_command(update, SimpleNamespace(bot=bot))

    verify.assert_awaited_once_with(bot, 17, sponsor_row)
    assert any("عضویت شما تأیید شد" in text for text, _ in message.replies)


@pytest.mark.asyncio
async def test_start_reports_membership_api_failure_without_granting_access(monkeypatch):
    db = SimpleNamespace(upsert_user=lambda **kwargs: None)
    sponsor_row = {"channel_id": -100123}
    monkeypatch.setattr(start, "get_db", lambda: db)
    monkeypatch.setattr(start, "get_active_sponsors", lambda: [sponsor_row])
    monkeypatch.setattr(
        start,
        "verify_membership",
        AsyncMock(side_effect=MembershipCheckError("API unavailable")),
    )
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=17, username="tester", first_name="Test"),
        effective_chat=SimpleNamespace(id=17),
        message=message,
    )

    await start.start_command(update, SimpleNamespace(bot=SimpleNamespace()))

    assert any("خطا" in text for text, _ in message.replies)
    assert all("عضویت شما تأیید شد" not in text for text, _ in message.replies)


@pytest.mark.asyncio
async def test_direct_market_callback_denies_nonmember(monkeypatch):
    query = FakeQuery("crypto", 17)
    sponsor_row = {"channel_id": -100123}
    get_prices = AsyncMock(return_value={})
    monkeypatch.setattr(user, "get_active_sponsors", lambda: [sponsor_row])
    monkeypatch.setattr(user, "verify_membership", AsyncMock(return_value=False))
    monkeypatch.setattr(user, "fetch_crypto_prices", get_prices)
    monkeypatch.setattr(user, "_update_user_activity", lambda user_id: None)

    await user.handle_user_callback(
        _callback_update(query),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    get_prices.assert_not_awaited()
    assert any("عضو" in text for text, _ in query.edits)


@pytest.mark.asyncio
async def test_membership_retry_callback_reports_api_error(monkeypatch):
    query = FakeQuery("check_membership", 17)
    monkeypatch.setattr(user, "get_active_sponsors", lambda: [{"channel_id": -100123}])
    monkeypatch.setattr(
        user,
        "verify_membership",
        AsyncMock(side_effect=MembershipCheckError("verification unavailable")),
    )

    await user.handle_user_callback(
        _callback_update(query),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    assert any("تلاش" in text or "خطا" in text for text, _ in query.edits)
    assert all("تأیید شد" not in text for text, _ in query.edits)


def test_owner_is_derived_only_from_a_valid_configured_id(monkeypatch):
    db = SimpleNamespace(get_admin_role=lambda user_id: None)
    monkeypatch.setattr(admin, "get_db", lambda: db)
    monkeypatch.setattr(admin, "SETTINGS", SimpleNamespace(owner_id=17))

    assert admin._authorized_role(17) == "OWNER"
    assert admin._authorized_role(18) is None

    monkeypatch.setattr(admin, "SETTINGS", SimpleNamespace(owner_id=0))
    assert admin._authorized_role(17) is None


@pytest.mark.asyncio
async def test_admin_settings_never_displays_bot_token(monkeypatch):
    secret = "test-token-must-not-appear"
    monkeypatch.setattr(admin, "SETTINGS", SimpleNamespace(owner_id=17))
    monkeypatch.setattr(
        admin,
        "get_settings",
        lambda: SimpleNamespace(
            bot_token=secret,
            database_path="test.db",
            cache_ttl=30,
        ),
    )
    monkeypatch.setattr(admin, "get_db", lambda: SimpleNamespace())
    query = FakeQuery("admin_settings", 17)

    await admin.admin_callback_handler(
        _callback_update(query),
        SimpleNamespace(),
    )

    assert secret not in query.edits[0][0]
    assert "تنظیم‌شده" in query.edits[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "callback_data"),
    [
        (admin, "admin_stats"),
        (sponsor, "sponsor_list"),
        (broadcast, "broadcast_start"),
    ],
)
async def test_management_callbacks_deny_users_without_admin_role(
    tmp_path, monkeypatch, module, callback_data
):
    db = Database(str(tmp_path / "dideban.db"))
    db.add_admin(999, "tester", role="MODERATOR")
    monkeypatch.setattr(admin, "get_db", lambda: db)
    monkeypatch.setattr(admin, "SETTINGS", SimpleNamespace(owner_id=None))

    query = FakeQuery(callback_data, 999)
    handler = {
        admin: admin.admin_callback_handler,
        sponsor: sponsor.sponsor_callback_handler,
        broadcast: broadcast.broadcast_callback_handler,
    }[module]
    await handler(_callback_update(query), SimpleNamespace())

    assert query.answers
    assert query.answers[0][1].get("show_alert") is True
