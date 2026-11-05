"""Реестр функций бота и их настроек по умолчанию."""

# Глобальные функции (включаются/выключаются администратором бота в /admin)
GLOBAL_FEATURES: dict[str, str] = {
    "moderation": "🛡 Ассистент групп: модерация",
    "call": "📢 Калл (пинг всех)",
    "economy": "💰 Экономика и казино",
    "games": "🎲 Мини-игры",
    "rp": "🤗 RP-команды и браки",
    "boardgames": "♟ Шахматы и шашки",
    "styles": "✨ Режимы текста в группах",
    "nicks": "🏷 Автоотметки по именам",
    "business": "💬 Ассистент чатов",
    "gifts": "🎁 Отслеживание подарков",
    "usernames": "🔎 Отслеживание юзернеймов",
}

# Настройки группы (меню /settings в группе)
GROUP_DEFAULTS: dict[str, object] = {
    "moderation": True,
    "call": True,
    "call_admins_only": True,
    "economy": True,
    "games": True,
    "rp": True,
    "boardgames": True,
    "styles": True,
    "nicks": False,
    "warn_limit": 3,
    "warn_action": "mute",  # mute | kick | ban
}

GROUP_TOGGLES: dict[str, str] = {
    "moderation": "🛡 Модерация",
    "call": "📢 Калл",
    "call_admins_only": "👮 Калл только для админов",
    "economy": "💰 Экономика/казино",
    "games": "🎲 Мини-игры",
    "rp": "🤗 RP и браки",
    "boardgames": "♟ Шахматы/шашки",
    "styles": "✨ Режимы текста (.пикми и др.)",
    "nicks": "🏷 Автоотметки по именам",
}

# Личные настройки пользователя (ассистент чатов)
USER_DEFAULTS: dict[str, object] = {
    "biz_edits": True,
    "biz_deletes": True,
    "biz_disappearing": True,
    "biz_mute": True,
    "biz_games": True,
    "msg_format": "none",  # формат: жирный, курсив и т.д.
}

USER_TOGGLES: dict[str, str] = {
    "biz_edits": "✏️ Уведомлять об изменениях",
    "biz_deletes": "🗑 Уведомлять об удалениях",
    "biz_disappearing": "🔥 Сохранять исчезающие медиа",
    "biz_mute": "🔇 Команды .мут/.размут",
    "biz_games": "♟ Команды .шахматы/.шашки",
}
