"""Сборщик рейсов из Telegram-канала.

Читает посты за последние 24 часа, разбирает их и записывает docs/deals.json.
Всё, что опубликовано раньше, в файл не попадает — так рейсы исчезают из
приложения через сутки после публикации.

Откуда брать рейсы, сколько их показывать и на какой WhatsApp писать —
задаётся в файле sources.json. Чтобы сменить или добавить канал, достаточно
поправить этот файл на GitHub: приложение читает только docs/deals.json.

Секреты (GitHub → Settings → Secrets and variables → Actions):
  TG_API_ID, TG_API_HASH — ключи приложения с https://my.telegram.org
  TG_SESSION             — строка входа, её печатает login_once.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import ChatInviteAlready, ChatInvite

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_parser import parse_post  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "deals.json"
CONFIG = ROOT / "sources.json"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"Не задана переменная {name}")
    return value


async def resolve_channel(client: TelegramClient, link: str):
    """Закрытый канал находим по ссылке-приглашению, открытый — по @имени."""
    m = re.search(r"(?:t\.me/\+|t\.me/joinchat/|^\+)([\w-]+)", link)
    if m:
        invite = await client(CheckChatInviteRequest(m.group(1)))
        if isinstance(invite, ChatInviteAlready):
            return invite.chat
        if isinstance(invite, ChatInvite):
            sys.exit("Аккаунт не подписан на канал. Вступите в него по ссылке с этого аккаунта.")
        sys.exit("Не удалось открыть ссылку-приглашение.")
    return await client.get_entity(link)


def load_config() -> dict:
    config = json.loads(CONFIG.read_text("utf-8"))
    sources = [src for src in config.get("sources", []) if src.get("enabled", True)]
    if not sources:
        sys.exit("В sources.json нет включённых источников")
    config["sources"] = sources
    return config


async def main() -> None:
    api_id = int(env("TG_API_ID"))
    api_hash = env("TG_API_HASH")
    session = env("TG_SESSION")
    config = load_config()
    ttl = timedelta(hours=float(config.get("ttlHours") or 24))

    now = datetime.now(timezone.utc)
    since = now - ttl

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Строка входа TG_SESSION устарела. Запустите login_once.py ещё раз.")

    deals = []
    report = []
    for n, source in enumerate(config["sources"]):
        agency = source.get("name") or "Агентство"
        try:
            channel = await resolve_channel(client, source["telegram"])
        except (SystemExit, Exception) as e:  # noqa: BLE001 — один сломанный источник не должен ронять остальные
            report.append(f"{agency}: не прочитан — {e}")
            continue
        posts = 0
        found = 0
        async for message in client.iter_messages(channel, limit=300):
            if message.date < since:
                break  # сообщения идут от новых к старым
            posts += 1
            text = message.message or ""
            # Номер источника в id, чтобы посты разных каналов не совпадали.
            for deal in parse_post(text, message.id, message.date, agency, ttl):
                if datetime.fromisoformat(deal.expiresAt.replace("Z", "+00:00")) > now:
                    item = deal.to_json()
                    item["id"] = f"{n}-{item['id']}"
                    deals.append(item)
                    found += 1
        report.append(f"{agency}: постов {posts}, рейсов {found}")
    await client.disconnect()
    print("\n".join(report))

    if not deals and all("постов" not in line for line in report):
        sys.exit("Ни один источник не прочитан — файл не меняем")

    deals.sort(key=lambda d: (d["departure"], d["price"]))
    settings = {"whatsApp": config.get("whatsApp"), "ttlHours": ttl.total_seconds() / 3600}
    payload = {
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": ", ".join(s.get("name", "") for s in config["sources"]),
        "settings": settings,
        "deals": deals,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    old = None
    if OUTPUT.exists():
        try:
            old = json.loads(OUTPUT.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    # Если ничего не поменялось, файл не трогаем — лишних коммитов не будет.
    if old and old.get("deals") == deals and old.get("settings") == settings:
        print("Без изменений")
        return
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"Файл обновлён, рейсов: {len(deals)}")


if __name__ == "__main__":
    asyncio.run(main())
