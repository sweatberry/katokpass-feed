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


async def find_in_dialogs(client: TelegramClient, channel_id: int | None, title: str | None):
    """Ищет канал среди подписок аккаунта — по номеру или по части названия."""
    wanted = (title or "").lower()
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not getattr(entity, "broadcast", False) and not getattr(entity, "megagroup", False):
            continue
        if channel_id is not None and entity.id == channel_id:
            return entity
        if channel_id is None and wanted and wanted in (dialog.name or "").lower():
            return entity
    return None


async def resolve_channel(client: TelegramClient, source: dict):
    """Порядок поиска: номер канала → часть названия → ссылка-приглашение → @имя.

    В sources.json:
      "telegram": "-1001234567890"  — номер канала (его показывает find_channel.py)
      "title": "чартер"             — часть названия канала среди подписок
      "telegram": "https://t.me/+…" — ссылка-приглашение (перестаёт работать, когда истекает)
    """
    link = str(source.get("telegram") or "").strip()
    if "ВСТАВЬТЕ" in link:
        raise RuntimeError("в sources.json не вписан номер канала — запустите find_channel.py")
    title = source.get("title")

    raw_id = link[4:] if link.startswith("-100") else link
    if raw_id.isdigit():
        entity = await find_in_dialogs(client, int(raw_id), None)
        if entity:
            return entity
        raise RuntimeError("канал с таким номером не найден среди подписок аккаунта")

    if title:
        entity = await find_in_dialogs(client, None, title)
        if entity:
            return entity

    m = re.search(r"(?:t\.me/\+|t\.me/joinchat/|^\+)([\w-]+)", link)
    if m:
        invite = await client(CheckChatInviteRequest(m.group(1)))
        if isinstance(invite, ChatInviteAlready):
            return invite.chat
        if isinstance(invite, ChatInvite):
            raise RuntimeError("аккаунт не подписан на канал — вступите в него по ссылке")
        raise RuntimeError("не удалось открыть ссылку-приглашение")
    if link:
        return await client.get_entity(link)
    raise RuntimeError("в sources.json не указан ни номер, ни название канала")


ALMATY = timezone(timedelta(hours=5))


def departs_too_soon(departure: str, now: datetime, cutoff_hour: int) -> bool:
    """После cutoff_hour по Алматы рейсы с вылетом сегодня уже не показываем."""
    local = now.astimezone(ALMATY)
    return departure == local.date().isoformat() and local.hour >= cutoff_hour


DEFAULT_MARKUP_PERCENT = 3.0


def with_markup(price: int, percent: float) -> int:
    """Цена для приложения: +percent % к цене из канала, округлённая вверх до 100 ₸."""
    raised = price * (100 + percent) / 100
    return int(-(-raised // 100) * 100)


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
    # Убираем пробелы и переносы, которые могли попасть при копировании.
    session = "".join(env("TG_SESSION").split()).strip("\"'")
    if len(session) not in (353, 369):
        sys.exit(f"Секрет TG_SESSION повреждён: {len(session)} символов вместо 353. "
                 "Запустите login_qr.py ещё раз — строка сама скопируется в буфер — и обновите секрет.")
    config = load_config()
    ttl = timedelta(hours=float(config.get("ttlHours") or 24))
    cutoff_hour = int(config.get("sameDayCutoffHour") or 16)
    markup = float(config.get("markupPercent", DEFAULT_MARKUP_PERCENT))

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
            channel = await resolve_channel(client, source)
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
                if datetime.fromisoformat(deal.expiresAt.replace("Z", "+00:00")) > now \
                        and not departs_too_soon(deal.departure, now, cutoff_hour):
                    item = deal.to_json()
                    item["price"] = with_markup(item["price"], markup)
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
