"""Бот целиком: настоящий Dispatcher aiogram, подменённая сеть Telegram и управляемые часы."""
import asyncio
import time as _time
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat, Message, Update

from reminder.bot import build_router, deliver_due
from reminder.storage import Store

CHAT = 777
WED_NOON_MSK = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)   # 12:00 по Москве


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.blocked = set()

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage) and method.chat_id in self.blocked:
            raise TelegramForbiddenError(method=method, message="bot was blocked by the user")
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.requests), date=int(_time.time()),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        yield b""

    def sent(self):
        return [r for r in self.requests if isinstance(r, SendMessage)]


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Harness:
    def __init__(self, store=None, now=WED_NOON_MSK):
        self.store = store or Store()
        self.clock = Clock(now)
        self.session = FakeSession()
        self.bot = Bot("123:TEST", session=self.session)
        self.dp = Dispatcher()
        self.dp.include_router(build_router(self.store, self.clock))
        self._n = 0

    def _feed(self, payload):
        self._n += 1
        before = len(self.session.requests)
        asyncio.run(self.dp.feed_update(self.bot, Update.model_validate({"update_id": self._n, **payload})))
        return self.session.requests[before:]

    def say(self, text):
        msg = {"message_id": self._n + 1, "date": int(_time.time()), "text": text,
               "chat": {"id": CHAT, "type": "private"}, "from": {"id": CHAT, "is_bot": False, "first_name": "Аня"}}
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        return self._feed({"message": msg})

    def press(self, data, text="🔔 Напоминание"):
        return self._feed({"callback_query": {
            "id": str(self._n), "chat_instance": "x", "data": data,
            "from": {"id": CHAT, "is_bot": False, "first_name": "Аня"},
            "message": {"message_id": 5, "date": int(_time.time()), "text": text,
                        "chat": {"id": CHAT, "type": "private"}}}})

    def tick(self, minutes=0):
        self.clock.now += timedelta(minutes=minutes)
        before = len(self.session.requests)
        asyncio.run(deliver_due(self.bot, self.store, self.clock.now))
        return [r for r in self.session.requests[before:] if isinstance(r, SendMessage)]


def test_message_becomes_reminder_with_confirmation():
    h = Harness()
    [reply] = h.say("завтра в 15:00 позвонить клиенту")
    assert "Напомню завтра в 15:00" in reply.text and "Позвонить клиенту" in reply.text
    [r] = h.store.upcoming(CHAT)
    assert r.due == datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)      # 15:00 МСК = 12:00 UTC


def test_unclear_text_gets_hint():
    h = Harness()
    [reply] = h.say("позвонить маме")
    assert "Не понял, когда" in reply.text and h.store.upcoming(CHAT) == []


def test_fires_at_the_right_moment_not_earlier():
    h = Harness()
    h.say("через 20 минут выключить духовку")
    assert h.tick(19) == []
    [msg] = h.tick(1)
    assert "Выключить духовку" in msg.text and "опозданием" not in msg.text
    assert h.store.upcoming(CHAT) == [] and h.tick(60) == []        # разовое — только один раз


def test_repeating_is_rescheduled():
    h = Harness()
    h.say("каждый день в 12:30 обед")
    [first] = h.tick(30)
    assert "Обед" in first.text and "каждый день" in first.text
    [r] = h.store.upcoming(CHAT)
    assert r.due == datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc)   # завтра в 12:30 МСК
    assert h.tick(60 * 24) and h.store.upcoming(CHAT)[0].due.day == 9


def test_survives_restart_and_reports_delay(tmp_path):
    db = str(tmp_path / "r.sqlite")
    h = Harness(store=Store(db))
    h.say("через 10 минут созвон")
    # бот «упал» и поднялся через час: новый процесс, та же база
    h2 = Harness(store=Store(db), now=WED_NOON_MSK + timedelta(hours=1))
    [msg] = h2.tick()
    assert "Созвон" in msg.text and "С опозданием на 50 мин" in msg.text


def test_snooze_one_time():
    h = Harness()
    h.say("через 5 минут чай")
    h.tick(5)
    rid = h.store.db.execute("SELECT id FROM reminders").fetchone()[0]
    calls = h.press(f"snooze:{rid}:10")
    assert any(isinstance(c, EditMessageText) and "Отложено до 12:15" in c.text for c in calls)
    assert h.tick(9) == [] and len(h.tick(1)) == 1


def test_snooze_repeating_keeps_schedule():
    h = Harness()
    h.say("каждый день в 12:05 витамины")
    h.tick(5)
    rid = h.store.upcoming(CHAT)[0].id
    h.press(f"snooze:{rid}:60")
    dues = sorted(r.due for r in h.store.upcoming(CHAT))
    assert dues == [datetime(2026, 10, 7, 10, 5, tzinfo=timezone.utc),     # отложенный раз через час
                    datetime(2026, 10, 8, 9, 5, tzinfo=timezone.utc)]      # а график — завтра как обычно


def test_list_and_delete():
    h = Harness()
    h.say("завтра в 10 созвон")
    h.say("каждую пятницу в 17:00 бэкап")
    [lst] = h.say("/list")
    assert "1. завтра в 10:00 — Созвон" in lst.text
    assert "в пятницу, 9 октября, в 17:00 (каждую пятницу) — Бэкап" in lst.text
    rid = h.store.upcoming(CHAT)[0].id
    h.press(f"del:{rid}")
    assert [r.text for r in h.store.upcoming(CHAT)] == ["Бэкап"]


def test_cannot_delete_someone_elses_reminder():
    h = Harness()
    other = h.store.add(999, "чужое", WED_NOON_MSK + timedelta(hours=1), None, "Europe/Moscow")
    h.press(f"del:{other.id}")
    assert h.store.upcoming(999)


def test_empty_list_for_new_user():
    """В оригинале /list у нового пользователя падал с KeyError."""
    [msg] = Harness().say("/list")
    assert "Напоминаний нет" in msg.text


def test_timezone_changes_meaning_of_time():
    h = Harness()
    h.press("tz:Asia/Novosibirsk")
    h.say("завтра в 15:00 планёрка")
    assert h.store.upcoming(CHAT)[0].due == datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)   # 15:00 по НСК = 08:00 UTC


def test_tz_by_name_and_wrong_name():
    h = Harness()
    assert "Europe/Berlin" in h.say("/tz Europe/Berlin")[0].text
    assert "Не знаю такого пояса" in h.say("/tz Марс/Олимп")[0].text


def test_blocked_user_reminder_is_dropped():
    h = Harness()
    h.say("через 1 минуту тест")
    h.session.blocked.add(CHAT)
    h.clock.now += timedelta(minutes=1)
    assert asyncio.run(deliver_due(h.bot, h.store, h.clock.now)) == 0     # не доставлено, бот не упал
    assert h.store.upcoming(CHAT) == []                                     # и больше не пытается


def test_html_in_text_is_escaped():
    h = Harness()
    h.say("через 1 минуту проверить <b>тег</b> & прочее")
    [msg] = h.tick(1)
    assert "&lt;b&gt;тег&lt;/b&gt; &amp; прочее" in msg.text
