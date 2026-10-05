"""Запуск: BOT_TOKEN=... python -m reminder"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv

from .bot import build_router, scheduler
from .storage import Store


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")
    store = Store(os.getenv("DB_PATH", "reminders.sqlite"))
    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(build_router(store))
    await bot.set_my_commands([BotCommand(command="list", description="Мои напоминания"),
                               BotCommand(command="tz", description="Часовой пояс"),
                               BotCommand(command="help", description="Как писать напоминания")])
    # планировщик берёт сроки из базы — после перезапуска ничего не теряется,
    # а пропущенное за время простоя придёт с пометкой об опоздании
    task = asyncio.create_task(scheduler(bot, store))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
