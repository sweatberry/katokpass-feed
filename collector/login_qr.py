"""Вход в Telegram по QR-коду — если код в Telegram не приходит.

Запуск (в Терминале, из папки feed-collector, с активированным .venv):
    pip install -r requirements.txt
    python collector/login_qr.py

На экране Mac откроется QR-код. На телефоне: Telegram → Настройки → Устройства →
«Подключить устройство» — и наведите камеру на код. В конце скрипт напечатает
строку для секрета TG_SESSION.

ВАЖНО: эта строка даёт полный доступ к аккаунту Telegram. Никому её не пересылайте.
"""
import asyncio
import getpass
import os
import subprocess
import tempfile

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

QR_FILE = os.path.join(tempfile.gettempdir(), "katokpass_login_qr.png")


def show_qr(url: str) -> None:
    qrcode.make(url).save(QR_FILE)
    # Откроется в «Просмотре» — так код легко отсканировать.
    subprocess.run(["open", QR_FILE], check=False)
    print("\nQR-код открыт на экране. На телефоне: Telegram → Настройки → Устройства →")
    print("«Подключить устройство» и наведите камеру на код.")
    print("Код обновляется примерно раз в 30 секунд — окно откроется заново.\n")


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
                password = getpass.getpass("У аккаунта включён облачный пароль. Введите его (символы не видны): ")
                await client.sign_in(password=password)
                break

        me = await client.get_me()
        print(f"\nВошли как {me.first_name} ({me.phone}).")
        print("\nСтрока для секрета TG_SESSION (скопируйте целиком, никому не пересылайте):\n")
        print(client.session.save())
        print()
    finally:
        await client.disconnect()
        if os.path.exists(QR_FILE):
            os.remove(QR_FILE)


if __name__ == "__main__":
    asyncio.run(main())
