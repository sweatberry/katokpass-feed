"""Показывает номера каналов, на которые подписан аккаунт.

Номер нужного канала вписывается в sources.json в поле "telegram".
Список печатается только здесь, на вашем Mac, — в логи GitHub он не попадает.

Запуск (в Терминале, из папки feed-collector, с активированным .venv):
    python collector/find_channel.py

Вход — по QR-коду, как в login_qr.py. В конце этот временный вход
автоматически завершается, секрет TG_SESSION на GitHub менять не нужно.
"""
import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from login_qr import QR_FILE, show_qr

HINTS = ("step", "степ", "чартер", "charter", "travel", "авиа")


async def main() -> None:
    api_id = int(input("api_id: ").strip())
    api_hash = input("api_hash: ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        qr = await client.qr_login()
        while True:
            show_qr(qr.url)
            try:
                await qr.wait(timeout=25)
                break
            except asyncio.TimeoutError:
                await qr.recreate()
            except SessionPasswordNeededError:
                password = getpass.getpass("Облачный пароль (символы не видны): ")
                await client.sign_in(password=password)
                break

        likely, other = [], []
        async for dialog in client.iter_dialogs():
            e = dialog.entity
            if not getattr(e, "broadcast", False) and not getattr(e, "megagroup", False):
                continue
            row = (f"-100{e.id}", dialog.name or "")
            (likely if any(h in row[1].lower() for h in HINTS) else other).append(row)

        print("\nПохоже на нужный канал:" if likely else "\nКаналов со словами step / чартер / travel не найдено.")
        for num, name in likely:
            print(f"  {num}   {name}")
        print("\nОстальные каналы и группы:")
        for num, name in other:
            print(f"  {num}   {name}")
        print("\nСкопируйте номер нужного канала (вместе с -100) и вставьте его в sources.json в поле \"telegram\".\n")

        await client.log_out()  # временный вход больше не нужен
    finally:
        if client.is_connected():
            await client.disconnect()
        if os.path.exists(QR_FILE):
            os.remove(QR_FILE)


if __name__ == "__main__":
    asyncio.run(main())
