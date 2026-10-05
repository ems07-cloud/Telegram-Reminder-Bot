"""Обработчики и планировщик напоминаний (aiogram 3)."""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .parser import ParseError, describe_repeat, describe_when, next_occurrence, parse
from .storage import Reminder, Store

log = logging.getLogger(__name__)

HELP = (
    "Напишите, о чём и когда напомнить, — обычными словами:\n\n"
    "• завтра в 15:00 позвонить клиенту\n"
    "• через 20 минут выключить духовку\n"
    "• в пятницу в 9:30 планёрка\n"
    "• 15 октября в 18:00 день рождения Лены\n"
    "• каждый понедельник в 9 отправить отчёт\n"
    "• по будням в 8:30 зарядка\n"
    "• каждое 1 число оплатить аренду\n\n"
    "/list — мои напоминания\n/tz — часовой пояс"
)

ZONES = [("Калининград", "Europe/Kaliningrad"), ("Москва", "Europe/Moscow"), ("Самара", "Europe/Samara"),
         ("Екатеринбург", "Asia/Yekaterinburg"), ("Омск", "Asia/Omsk"), ("Новосибирск", "Asia/Novosibirsk"),
         ("Красноярск", "Asia/Krasnoyarsk"), ("Иркутск", "Asia/Irkutsk"), ("Якутск", "Asia/Yakutsk"),
         ("Владивосток", "Asia/Vladivostok"), ("Магадан", "Asia/Magadan"), ("Камчатка", "Asia/Kamchatka")]

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows])


def _line(r: Reminder, now_local: datetime) -> str:
    when = describe_when(r.due.astimezone(now_local.tzinfo), now_local)
    rep = describe_repeat(r.repeat)
    return f"{when}{' (' + rep + ')' if rep else ''} — {html.escape(r.text)}"


def build_router(store: Store, clock: Clock = utcnow) -> Router:
    router = Router()

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def start(message: Message):
        tz = store.tz(message.chat.id)
        await message.answer(HELP + f"\n\nСейчас часовой пояс: {tz.key}.")

    @router.message(Command("list"))
    async def list_(message: Message):
        items = store.upcoming(message.chat.id)
        if not items:
            return await message.answer("Напоминаний нет. Напишите, например: «завтра в 10 созвон с командой».")
        now_local = clock().astimezone(store.tz(message.chat.id))
        lines = [f"{i}. {_line(r, now_local)}" for i, r in enumerate(items, 1)]
        buttons = [(f"✖ {i}", f"del:{r.id}") for i, r in enumerate(items, 1)]
        rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        await message.answer("<b>Ваши напоминания</b>\n\n" + "\n".join(lines) + "\n\nУдалить — кнопкой с номером.",
                             reply_markup=_kb(*rows), parse_mode="HTML")

    @router.message(Command("tz"))
    async def tz(message: Message, command: CommandObject):
        if command.args:
            try:
                store.set_tz(message.chat.id, command.args.strip())
            except Exception:
                return await message.answer("Не знаю такого пояса. Пример: /tz Europe/Moscow или выберите кнопкой: /tz")
            return await message.answer(f"Часовой пояс: {command.args.strip()}")
        rows = [[(name, f"tz:{zone}") for name, zone in ZONES[i:i + 3]] for i in range(0, len(ZONES), 3)]
        await message.answer(f"Сейчас: {store.tz(message.chat.id).key}. Выберите город с вашим временем:",
                             reply_markup=_kb(*rows))

    @router.callback_query(F.data.startswith("tz:"))
    async def tz_pick(cb: CallbackQuery):
        zone = cb.data[3:]
        store.set_tz(cb.message.chat.id, zone)
        name = dict((z, n) for n, z in ZONES).get(zone, zone)
        await cb.message.edit_text(f"Часовой пояс: {name} ({zone}). Напоминания будут приходить по этому времени.")
        await cb.answer()

    @router.message(F.text & ~F.text.startswith("/"))
    async def add(message: Message):
        tz = store.tz(message.chat.id)
        now_local = clock().astimezone(tz)
        try:
            p = parse(message.text, now_local)
        except ParseError as e:
            return await message.reply(str(e))
        r = store.add(message.chat.id, p.text, p.when, p.repeat, tz.key)
        rep = describe_repeat(p.repeat)
        await message.reply(
            f"⏰ Напомню {describe_when(p.when, now_local)}{', дальше ' + rep if rep else ''}:\n"
            f"<b>{html.escape(p.text)}</b>",
            reply_markup=_kb([("Отменить", f"del:{r.id}")]), parse_mode="HTML")

    @router.callback_query(F.data.startswith("del:"))
    async def delete(cb: CallbackQuery):
        rid = int(cb.data[4:])
        r = store.get(rid)
        if store.delete(rid, cb.message.chat.id):
            await cb.message.answer(f"Удалил: {r.text}")
            await cb.answer("Удалено")
        else:
            await cb.answer("Уже удалено или сработало", show_alert=False)

    @router.callback_query(F.data.startswith("snooze:"))
    async def snooze(cb: CallbackQuery):
        _, rid, minutes = cb.data.split(":")
        r = store.get(int(rid))
        if not r or r.chat_id != cb.message.chat.id:
            return await cb.answer("Не нашёл напоминание")
        due = clock() + timedelta(minutes=int(minutes))
        if r.repeat:
            # у повторяющегося отложенный раз — отдельное разовое напоминание, график не сбиваем
            store.add(r.chat_id, r.text, due.astimezone(store.tz(r.chat_id)), None, r.tz)
        else:
            store.reschedule(r.id, due)
        local = due.astimezone(store.tz(r.chat_id))
        await cb.message.edit_text(cb.message.html_text + f"\n\n😴 Отложено до {local:%H:%M}", parse_mode="HTML")
        await cb.answer()

    @router.callback_query(F.data.startswith("done:"))
    async def done(cb: CallbackQuery):
        await cb.message.edit_text(cb.message.html_text + "\n\n✅ Готово", parse_mode="HTML")
        await cb.answer()

    return router


async def deliver_due(bot: Bot, store: Store, now: datetime, grace: timedelta = timedelta(minutes=2)) -> int:
    """Отправляет всё, чему пора сработать. Возвращает число отправленных.
    Напоминания, пропущенные пока бот был выключен, тоже приходят — с пометкой, на сколько опоздали."""
    sent = 0
    for r in store.due(now):
        late = now - r.due
        text = f"🔔 <b>Напоминание</b>\n\n{html.escape(r.text)}"
        if late > grace:
            mins = int(late.total_seconds() // 60)
            ago = f"{mins // 60} ч {mins % 60:02d} мин" if mins >= 60 else f"{mins} мин"
            text += f"\n\n<i>С опозданием на {ago}: бот был недоступен.</i>"
        if r.repeat:
            text += f"\n🔁 {describe_repeat(r.repeat)}"
        kb = _kb([("+10 мин", f"snooze:{r.id}:10"), ("+1 час", f"snooze:{r.id}:60"), ("Готово", f"done:{r.id}")])
        try:
            await bot.send_message(r.chat_id, text, reply_markup=kb, parse_mode="HTML")
            sent += 1
        except TelegramForbiddenError:
            log.info("чат %s заблокировал бота — напоминание %s снято", r.chat_id, r.id)
            store.finish(r.id)
            continue
        except TelegramAPIError:
            log.exception("не отправилось напоминание %s, попробую в следующий раз", r.id)
            continue
        if r.repeat:
            local_now = max(now, r.due).astimezone(store.tz(r.chat_id))
            store.reschedule(r.id, next_occurrence(r.repeat, r.at, local_now))
        else:
            store.finish(r.id)
    return sent


async def scheduler(bot: Bot, store: Store, clock: Clock = utcnow, every: float = 15) -> None:
    while True:
        try:
            await deliver_due(bot, store, clock())
        except Exception:
            log.exception("ошибка планировщика")
        await asyncio.sleep(every)
