from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int, need_restrict: bool = False) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    if member.status == ChatMemberStatus.CREATOR:
        return True
    if member.status == ChatMemberStatus.ADMINISTRATOR:
        return member.can_restrict_members if need_restrict else True
    return False


async def message_from_admin(message: Message, bot: Bot, need_restrict: bool = False) -> bool:
    # Анонимный администратор пишет от имени самого чата
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    if not message.from_user:
        return False
    return await is_chat_admin(bot, message.chat.id, message.from_user.id, need_restrict)
