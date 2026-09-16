"""Один раз войти в Telegram и получить строку входа для сборщика.

Запуск на Mac (в Терминале, из папки feed-collector):
    pip3 install -r requirements.txt
    python3 collector/login_once.py

Скрипт спросит api_id и api_hash (с https://my.telegram.org → API development tools),
номер телефона и код из Telegram. В конце он напечатает длинную строку —
её нужно сохранить на GitHub как секрет TG_SESSION.

ВАЖНО: эта строка даёт полный доступ к аккаунту Telegram. Никому её не пересылайте
и не вставляйте в код. Надёжнее завести для сборщика отдельный аккаунт,
подписанный только на нужные каналы.
"""
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(input("api_id: ").strip())
api_hash = input("api_hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    me = client.get_me()
    print(f"\nВошли как {me.first_name} ({me.phone}).")
    print("\nСтрока для секрета TG_SESSION (скопируйте целиком):\n")
    print(client.session.save())
    print()
