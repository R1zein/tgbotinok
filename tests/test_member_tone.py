from test_handlers import GROUP, USER, OTHER, env, msg, upd
from bot.db import Database
from bot.handlers.ai import requested_calm


async def test_personal_tone(env, monkeypatch):
    dp, bot, session, db, feed = env
    await db.set_chat_setting(GROUP, "ai", True)
    await db.set_chat_setting(GROUP, "ai_persona", "adaptive")
    await db.set_chat_setting(GROUP, "ai_cooldown", 0)
    prompts = []

    async def complete(messages, **kwargs):
        prompts.append(messages[0]["content"])
        return "ответ"

    monkeypatch.setattr(dp["ai"], "complete", complete)
    await feed(upd(message=msg(GROUP, USER, "бот, успокойся")))
    assert await db.calm_tone(GROUP, USER)
    assert not await db.calm_tone(GROUP - 1, USER)
    await feed(upd(message=msg(GROUP, USER, "ии как дела?")))
    await feed(upd(message=msg(GROUP, OTHER, "ии как дела?")))
    assert "попросил спокойный тон" in prompts[-2]
    assert "попросил спокойный тон" not in prompts[-1]
    reopened = Database(dp["config"].db_path)
    await reopened.connect()
    try:
        assert await reopened.calm_tone(GROUP, USER)
    finally:
        await reopened.close()
    await feed(upd(message=msg(GROUP, USER, "бот можно снова шутить")))
    assert not await db.calm_tone(GROUP, USER)
    assert requested_calm("он сказал: успокойся") is None
