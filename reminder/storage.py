"""Напоминания и часовые пояса в SQLite.

В оригинале напоминания жили только в памяти планировщика: после перезапуска бота ни одно
из них не срабатывало, хотя в reminder.json они оставались. Здесь срок хранится в базе,
а планировщик каждый раз берёт из неё то, что пора отправить."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Moscow"


@dataclass
class Reminder:
    id: int
    chat_id: int
    text: str
    due: datetime          # UTC
    repeat: str | None
    at: time | None        # время суток для повторов (в поясе пользователя)
    tz: str


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                due TEXT NOT NULL,
                repeat TEXT,
                at TEXT,
                tz TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS ix_due ON reminders(active, due);
            CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, tz TEXT NOT NULL);
        """)

    @staticmethod
    def _row(r) -> Reminder:
        return Reminder(r["id"], r["chat_id"], r["text"], datetime.fromisoformat(r["due"]), r["repeat"],
                        time.fromisoformat(r["at"]) if r["at"] else None, r["tz"])

    # --- часовые пояса ---
    def tz(self, chat_id: int) -> ZoneInfo:
        row = self.db.execute("SELECT tz FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return ZoneInfo(row["tz"] if row else DEFAULT_TZ)

    def set_tz(self, chat_id: int, tz: str) -> None:
        ZoneInfo(tz)  # проверка, что такой пояс есть
        self.db.execute("INSERT OR REPLACE INTO users VALUES (?, ?)", (chat_id, tz))
        self.db.commit()

    # --- напоминания ---
    def add(self, chat_id: int, text: str, when: datetime, repeat: str | None, tz: str) -> Reminder:
        at = when.timetz().replace(tzinfo=None).isoformat(timespec="minutes") if repeat else None
        cur = self.db.execute(
            "INSERT INTO reminders (chat_id, text, due, repeat, at, tz) VALUES (?,?,?,?,?,?)",
            (chat_id, text, when.astimezone(timezone.utc).isoformat(), repeat, at, tz))
        self.db.commit()
        return self.get(cur.lastrowid)

    def get(self, rid: int) -> Reminder | None:
        r = self.db.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
        return self._row(r) if r else None

    def upcoming(self, chat_id: int) -> list[Reminder]:
        rows = self.db.execute("SELECT * FROM reminders WHERE chat_id=? AND active=1 ORDER BY due",
                               (chat_id,)).fetchall()
        return [self._row(r) for r in rows]

    def due(self, now: datetime) -> list[Reminder]:
        rows = self.db.execute("SELECT * FROM reminders WHERE active=1 AND due<=? ORDER BY due",
                               (now.astimezone(timezone.utc).isoformat(),)).fetchall()
        return [self._row(r) for r in rows]

    def reschedule(self, rid: int, due: datetime) -> None:
        self.db.execute("UPDATE reminders SET due=?, active=1 WHERE id=?",
                        (due.astimezone(timezone.utc).isoformat(), rid))
        self.db.commit()

    def finish(self, rid: int) -> None:
        self.db.execute("UPDATE reminders SET active=0 WHERE id=?", (rid,))
        self.db.commit()

    def delete(self, rid: int, chat_id: int) -> bool:
        """Удалить можно только своё напоминание."""
        cur = self.db.execute("UPDATE reminders SET active=0 WHERE id=? AND chat_id=? AND active=1", (rid, chat_id))
        self.db.commit()
        return cur.rowcount > 0
