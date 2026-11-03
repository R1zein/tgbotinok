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
    from ..proxy import telethon_proxy

    client = TelegramClient(StringSession(), int(api_id), api_hash,
                            proxy=telethon_proxy(os.getenv("PROXY", "").strip() or None))
    await client.start()
    me = await client.get_me()
    print(f"\nВошли как {me.first_name} (@{me.username}).")
    session = client.session.save()
    out = os.getenv("SESSION_OUT")
    if out:
        # setup.sh сам перенесёт строку в .env — на экран её не выводим
        with open(out, "w") as f:
            f.write(session)
        os.chmod(out, 0o600)
        print("Строка сессии сохранена.")
    else:
        print("\nUSERBOT_SESSION=" + session)
        print("\nНикому не передавайте эту строку — она даёт полный доступ к аккаунту.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
