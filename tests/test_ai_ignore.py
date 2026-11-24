from test_handlers import ADMIN, GROUP, USER, BOT_ID, cb, env, msg, upd
from bot.middlewares import REFUSAL


async def test_manage_ignore_and_group_routing(env):
    dp, bot, session, db, feed = env
    await db.set_chat_setting(GROUP, "ai", True)
    for action, enabled in (("ignore_add", True), ("ignore_remove", False)):
        await feed(cb(ADMIN, "adm:ignore:0", msg(ADMIN, BOT_ID, "панель")))
        await feed(cb(ADMIN, "adm:" + action, msg(ADMIN, BOT_ID, "игнор")))
        await feed(upd(message=msg(ADMIN, ADMIN, str(USER))))
        assert await db.ai_ignored(USER) == enabled
        if enabled:
            session.clear()
            for phrase in ("все", "все дома", "это всё"):
                await feed(upd(message=msg(GROUP, USER, phrase)))
            assert "SendMessage" not in session.names()
            assert "SetMessageReaction" not in session.names()
            await feed(upd(message=msg(GROUP, USER, "бот, помоги")))
            assert list(filter(None, session.texts())) == [REFUSAL]


async def test_remove_original_ignore_survives_restart(tmp_path):
    from bot.db import Database
    db = Database(str(tmp_path / "db"))
    await db.connect()
    try:
        assert await db.ai_ignored(5395604971)
        await db.set_ai_ignore(5395604971, False)
    finally:
        await db.close()
    await db.connect()
    try:
        assert not await db.ai_ignored(5395604971)
    finally:
        await db.close()
