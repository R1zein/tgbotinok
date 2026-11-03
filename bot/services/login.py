"""Создание строки сессии юзербота.

Запуск: python -m bot.services.login
Полученную строку вставьте в USERBOT_SESSION в .env.
Рекомендуется использовать отдельный (технический) аккаунт.
"""
import asyncio

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    import os

    load_dotenv()
    api_id = os.getenv("API_ID") or input("API_ID: ")
    api_hash = os.getenv("API_HASH") or input("API_HASH: ")
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()
    me = await client.get_me()
    print(f"\nВошли как {me.first_name} (@{me.username}).")
    print("\nUSERBOT_SESSION=" + client.session.save())
    print("\nНикому не передавайте эту строку — она даёт полный доступ к аккаунту.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
