from test_handlers import ADMIN, GROUP, USER, BOT_ID, cb, env, msg, upd
from bot.middlewares import REFUSAL


async def test_manage_ignore_and_group_routing(env):
    dp, bot, session, db, feed = env
    await db.set_chat_setting(GROUP, "ai", True)
    await db.touch_member(GROUP, USER, "User", "known_user")
    for enabled in (True, False):
        await feed(cb(ADMIN, "adm:black", msg(ADMIN, BOT_ID, "панель")))
        if enabled:
            await feed(cb(ADMIN, "adm:ban", msg(ADMIN, BOT_ID, "список")))
            await feed(upd(message=msg(ADMIN, ADMIN, "@Known_User")))
        else:
            await feed(cb(ADMIN, "adm:pick:0", msg(ADMIN, BOT_ID, "список")))
            await feed(cb(ADMIN, f"adm:release:{USER}:0", msg(ADMIN, BOT_ID, "выбор")))
        assert await db.is_banned(USER) == enabled
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
        assert await db.is_banned(5395604971)
        await db.set_banned(5395604971, False)
    finally:
        await db.close()
    await db.connect()
    try:
        assert not await db.is_banned(5395604971)
    finally:
        await db.close()

async def test_blacklist_pages(env):
    from bot.handlers.admin import blacklist_screen
    _, _, _, db, _ = env
    for uid in range(20, 32):
        await db.set_banned(uid, True)
    text, kb = await blacklist_screen(db)
    assert text.splitlines()[1:] == [str(i) for i in range(31, 21, -1)]
    assert kb.inline_keyboard[0][0].text == "Все"
    _, kb = await blacklist_screen(db, "pick", 1)
    assert kb.inline_keyboard[0][0].callback_data == "adm:release:21:1"


async def test_merge_existing_blacklists(tmp_path):
    import sqlite3
    from bot.db import Database, SCHEMA
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.executescript("""
            CREATE TABLE ai_ignore(user_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
            INSERT INTO ai_ignore VALUES(5395604971,0),(31,1),(32,1);
            INSERT INTO users(id,is_banned) VALUES(30,1),(32,1);
        """)
    db = Database(path)
    await db.connect()
    try:
        assert [r["id"] for r in await db.blacklist_page(0)] == [30,31,32]
        await db.set_banned(32, False)
    finally:
        await db.close()
    await db.connect()
    try:
        assert not await db.is_banned(32)
    finally:
        await db.close()
