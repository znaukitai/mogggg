import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import admin
import config
import db
import handlers
from matchmaking import run_loop

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    if config.BOT_TOKEN.startswith("8808641162:AAE4-Pzv6M6LXsVPO2w4kWqv2CDvqGiYELU"):
        raise SystemExit("Set the BOT_TOKEN environment variable (token from @BotFather).")

    await db.init()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(admin.router)     # admin first: its FSM states must win over user handlers
    dp.include_router(handlers.router)

    on_match, on_timeout = handlers.make_callbacks(bot, storage)
    matcher = asyncio.create_task(run_loop(on_match, on_timeout))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        matcher.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
