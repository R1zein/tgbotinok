import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from .config import load_config
from .db import Database
from .handlers import get_routers
from .handlers.ai import AIMemoryMiddleware
from .handlers.nicks import NickMentionMiddleware
from .services.ai import AIClient
from .middlewares import TrackMiddleware
from .services.gifts import GiftWatcher
from .services.resale import ResaleWatcher
from .services.userbot import Userbot
from .services.usernames import UsernameWatcher

log = logging.getLogger("bot")


async def cleanup_loop(db: Database, days: int) -> None:
    while True:
        try:
            await db.cleanup_business_messages(days)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки кэша")
        await asyncio.sleep(6 * 3600)


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Список команд"),
    ], scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands([
        BotCommand(command="settings", description="Настройки группы"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="mute", description="Заглушить (ответом)"),
        BotCommand(command="ban", description="Заблокировать (ответом)"),
        BotCommand(command="kick", description="Исключить (ответом)"),
        BotCommand(command="warn", description="Предупреждение (ответом)"),
        BotCommand(command="call", description="Позвать всех"),
        BotCommand(command="chess", description="Шахматы"),
        BotCommand(command="checkers", description="Шашки"),
    ], scope=BotCommandScopeAllGroupChats())


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    db = Database(config.db_path)
    await db.connect()

    session = AiohttpSession(proxy=config.proxy) if config.proxy else None
    if config.proxy:
        log.info("Работаю через прокси %s", config.proxy.split("@")[-1])
    bot = Bot(config.bot_token, session=session, default=DefaultBotProperties(
        parse_mode=ParseMode.HTML, link_preview_is_disabled=True,
    ))
    userbot = Userbot(config)
    await userbot.start()
    username_watcher = UsernameWatcher(bot, db, userbot, config.username_check_delay,
                                       config.username_cycle_interval, config.proxy)
    gift_watcher = GiftWatcher(bot, db, userbot, config.gift_poll_interval)
    resale_watcher = ResaleWatcher(bot, db, userbot, config.gift_poll_interval)

    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["config"] = config
    dp["userbot"] = userbot
    dp["username_watcher"] = username_watcher
    ai_client = AIClient(config)
    dp["ai"] = ai_client
    track = TrackMiddleware(db, config)
    dp.message.outer_middleware(track)
    dp.callback_query.outer_middleware(track)
    dp.message.outer_middleware(NickMentionMiddleware())
    dp.message.outer_middleware(AIMemoryMiddleware())
    dp.include_routers(*get_routers())

    tasks = [
        asyncio.create_task(gift_watcher.run()),
        asyncio.create_task(resale_watcher.run()),
        asyncio.create_task(username_watcher.run()),
        asyncio.create_task(cleanup_loop(db, config.business_cache_days)),
    ]
    try:
        await set_commands(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await userbot.stop()
        await username_watcher.close()
        await ai_client.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
