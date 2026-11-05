from aiogram import Router

from . import (
    admin,
    boardgames,
    business,
    call,
    duel,
    economy,
    fun,
    gifts,
    group_settings,
    group_styles,
    moderation,
    nicks,
    resale,
    rp,
    start,
    styles,
    usernames,
)


def get_routers() -> list[Router]:
    # Порядок важен: FSM и команды раньше, «ловящие всё» RP-действия — в конце
    return [
        admin.router,
        start.router,
        usernames.router,
        gifts.router,
        resale.router,
        styles.router,
        business.router,
        group_settings.router,
        nicks.router,
        moderation.router,
        call.router,
        economy.router,
        duel.router,
        boardgames.router,
        fun.router,
        rp.router,
        group_styles.router,  # последним: ловит обычный текст участников с режимом
    ]
