"""Авторские туры из канала Sweatberry → docs/signature.json и фото в docs/signature/.

Запускается из collect.py тем же входом в Telegram, что и сбор рейсов.
Канал задаётся в sources.json:  "signatureChannel": "https://t.me/sweatberry_app"

Как пост становится туром:
  • альбом с текстом-подписью — тур, фото из альбома;
  • альбом, а под ним текст ответом на альбом (если текст длиннее 1024 знаков) —
    тур с текстом из ответа и фото из альбома;
  • альбом без подписи и сразу следующим сообщением текст (не ответом) — то же самое;
  • просто текст без фото — тоже тур, вместо фото будет флажок.
Посты без дат и цены (анонсы, отзывы) пропускаются молча. Если пост похож на тур,
но чего-то не хватает, бот пишет об этом в adminChat — один раз на каждую правку поста.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from signature_parser import parse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "signature.json"
PHOTOS = ROOT / "docs" / "signature"
STATE = ROOT / "docs" / "signature_state.json"

HISTORY = 300          # сколько последних сообщений канала читать — туров там единицы
MAX_PHOTOS = 10
PHOTO_SIDE = 1600      # длинная сторона фото после сжатия
ALMATY = timezone(timedelta(hours=5))


# ── сообщения канала без Telethon — так связывание можно проверить тестами ────
@dataclass
class Msg:
    id: int
    text: str
    date: datetime
    grouped_id: int | None = None
    reply_to: int | None = None
    photo_id: int | None = None          # None — в сообщении нет картинки
    raw: object = None                   # исходное сообщение Telethon, чтобы скачать фото


@dataclass
class Post:
    text_msg: Msg
    photos: list[Msg] = field(default_factory=list)


def build_posts(messages: list[Msg]) -> list[Post]:
    """Собирает из сообщений канала посты: текст + фото, к которым он относится."""
    by_id = {m.id: m for m in messages}
    groups: dict[int, list[Msg]] = {}
    for m in messages:
        if m.grouped_id is not None:
            groups.setdefault(m.grouped_id, []).append(m)
    for g in groups.values():
        g.sort(key=lambda m: m.id)

    def photos_of(m: Msg) -> list[Msg]:
        if m.grouped_id is not None:
            return [x for x in groups[m.grouped_id] if x.photo_id is not None]
        return [m] if m.photo_id is not None else []

    def album_key(m: Msg):
        return ("g", m.grouped_id) if m.grouped_id is not None else ("m", m.id)

    posts: list[Post] = []
    claimed: set = set()       # альбомы, к которым уже привязан текст-ответ

    # 1. Текст ответом на альбом или фото — главный случай для длинных постов.
    for m in sorted(messages, key=lambda x: x.id):
        if m.photo_id is not None or not m.text.strip() or m.reply_to is None:
            continue
        target = by_id.get(m.reply_to)
        if target is None:
            continue
        photos = photos_of(target)
        if not photos:
            continue
        key = album_key(target)
        if key in claimed:
            continue                     # на альбом ответили дважды — берём первый ответ
        claimed.add(key)
        posts.append(Post(m, photos[:MAX_PHOTOS]))

    linked_texts = {p.text_msg.id for p in posts}

    # 1б. Текст отдельным сообщением сразу после альбома без подписи — не ответом.
    # Так выкладывать естественнее всего, поэтому понимаем и этот вариант:
    # альбом без подписи + следующее сообщение канала с текстом в пределах 30 минут.
    ordered = sorted(messages, key=lambda x: x.id)
    for i, m in enumerate(ordered):
        if i == 0 or m.photo_id is not None or not m.text.strip() or m.id in linked_texts:
            continue
        if m.reply_to is not None and m.reply_to in by_id:
            continue                     # явный ответ на что-то — разобран выше или это не наш случай
        prev = ordered[i - 1]
        if prev.photo_id is None:
            continue
        key = album_key(prev)
        if key in claimed:
            continue
        members = groups.get(prev.grouped_id, [prev]) if prev.grouped_id is not None else [prev]
        if any(x.text.strip() for x in members):
            continue                     # у альбома своя подпись — значит, это отдельный пост
        if abs((m.date - prev.date).total_seconds()) > 30 * 60:
            continue
        claimed.add(key)
        posts.append(Post(m, photos_of(prev)[:MAX_PHOTOS]))
        linked_texts.add(m.id)

    # 2. Альбом или одиночное фото с подписью.
    seen_albums: set = set()
    for m in sorted(messages, key=lambda x: x.id):
        if m.photo_id is None:
            continue
        key = album_key(m)
        if key in seen_albums or key in claimed:
            continue
        seen_albums.add(key)
        photos = photos_of(m)
        members = groups.get(m.grouped_id, [m]) if m.grouped_id is not None else [m]
        caption_msg = next((x for x in members if x.text.strip()), None)
        if caption_msg is not None:
            posts.append(Post(caption_msg, photos[:MAX_PHOTOS]))

    # 3. Текст без фото, который ни на что не отвечает.
    for m in messages:
        if m.photo_id is None and m.text.strip() and m.id not in linked_texts:
            target = by_id.get(m.reply_to) if m.reply_to else None
            if target is not None and photos_of(target):
                continue                 # это второй ответ на уже занятый альбом
            posts.append(Post(m, []))

    posts.sort(key=lambda p: p.text_msg.id)
    return posts


def looks_like_tour(text: str) -> bool:
    """Пост похож на тур, если в нём есть даты, цена или стоимость. Иначе молчим."""
    return bool(re.search(r"(даты?\s*:|стоимость\s*:|цена\s*(от)?\s*:|\$\s?\d|\d\s?\$|₸)", text or "", re.I))


def is_past(tour: dict, today) -> bool:
    """Тур уходит в день вылета. Если вариантов несколько — по самому позднему."""
    dates = [v.get("departure") for v in (tour.get("variants") or []) if v.get("departure")]
    if not dates and tour.get("departure"):
        dates = [tour["departure"]]
    return bool(dates) and max(dates) <= today.isoformat()


# ── фото ─────────────────────────────────────────────────────────────────────
def shrink(data: bytes) -> bytes:
    """Сжимаем до 1600 px по длинной стороне — вкладка грузится быстро, репозиторий не пухнет."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 — странный файл лучше сохранить как есть, чем потерять
        return data


# ── сообщения в Telegram от бота ──────────────────────────────────────────────
def notify(text: str) -> None:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = str(_config_cache.get("adminChat") or "").strip()
    if not token or not chat or "ВСТАВЬТЕ" in chat:
        print(f"[бот не настроен] {text}")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)
    except Exception as e:  # noqa: BLE001
        print(f"Не удалось отправить сообщение боту: {e}")


_config_cache: dict = {}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", "utf-8")


def to_msg(m) -> Msg:
    """Сообщение Telethon → Msg."""
    photo_id = None
    if getattr(m, "photo", None) is not None:
        photo_id = m.photo.id
    elif getattr(m, "document", None) is not None and str(getattr(m.file, "mime_type", "")).startswith("image/"):
        photo_id = m.document.id         # фото, отправленное файлом
    reply = getattr(m, "reply_to", None)
    return Msg(id=m.id, text=m.message or "", date=m.date, grouped_id=m.grouped_id,
               reply_to=getattr(reply, "reply_to_msg_id", None), photo_id=photo_id, raw=m)


def channel_link(entity, msg_id: int) -> str | None:
    username = getattr(entity, "username", None)
    return f"https://t.me/{username}/{msg_id}" if username else None


async def collect_signature(client, config: dict, now: datetime, resolve) -> None:
    """Читает канал авторских туров и обновляет docs/signature.json."""
    ref = str(config.get("signatureChannel") or "").strip()
    if not ref:
        print("Авторские туры: signatureChannel в sources.json не задан — пропускаю")
        return
    _config_cache.clear()
    _config_cache.update(config)

    entity = await resolve(client, {"telegram": ref})
    raw = [m async for m in client.iter_messages(entity, limit=HISTORY)]
    messages = [to_msg(m) for m in raw]
    posts = build_posts(messages)
    today = now.astimezone(ALMATY).date()

    state = load_json(STATE, {"reported": {}, "published": []})
    reported: dict = state.get("reported") or {}
    published: list = state.get("published") or []

    old_file = load_json(OUTPUT, {})
    previous = {t["id"]: t for t in old_file.get("tours", []) if isinstance(t, dict) and "id" in t}

    tours: list[dict] = []
    used_photos: set[str] = set()
    problems = 0
    for post in posts:
        text = post.text_msg.text
        tour_id = str(post.text_msg.id)
        result = parse(text, today, tour_id)
        if result.errors:
            kept = previous.get(tour_id)
            if kept is not None and not is_past(kept, today):
                # Тур уже был в приложении, а при правке поста что-то сломали.
                # Оставляем прежнюю версию: иначе тур пропадёт, а после исправления
                # придёт людям повторным анонсом как «новый».
                tours.append(kept)
                used_photos.update(Path(x).name for x in kept.get("photos") or [])
            if looks_like_tour(text):
                problems += 1
                digest = hashlib.sha1((text + "|".join(result.errors)).encode()).hexdigest()
                if reported.get(tour_id) != digest:
                    first = text.strip().split("\n", 1)[0][:80]
                    tail = ("\n\nВ приложении пока остаётся прежняя версия тура."
                            if kept is not None else "")
                    notify("Пост не попал в приложение 😕\n\n"
                           f"«{first}»\n\n" + "\n".join(f"• {e}" for e in result.errors) +
                           tail + "\n\nПоправьте пост — через 15 минут проверю снова.")
                    reported[tour_id] = digest
            continue
        reported.pop(tour_id, None)
        tour = result.tour
        if is_past(tour, today):
            continue

        photos = []
        for pm in post.photos:
            name = f"{pm.photo_id}.jpg"
            path = PHOTOS / name
            if not path.exists():
                data = await client.download_media(pm.raw, file=bytes)
                if not data:
                    continue
                PHOTOS.mkdir(parents=True, exist_ok=True)
                path.write_bytes(shrink(data))
            photos.append(f"signature/{name}")
            used_photos.add(name)
        tour["photos"] = photos
        tour["postedAt"] = post.text_msg.date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        tour["telegram"] = channel_link(entity, post.text_msg.id)
        tours.append(tour)

        if tour_id not in published:
            published.append(tour_id)
            extra = "" if photos else "\n\nФото не нашёл — в карточке будет флажок. Добавьте альбом и ответьте на него текстом."
            notify(f"Тур опубликован в приложении ✅\n\n«{tour['title']}»\nот {price_text(tour)} · "
                   f"{len(photos)} фото{extra}")

    # фото туров, которых больше нет, удаляем — им незачем лежать в репозитории
    if PHOTOS.exists():
        for f in PHOTOS.glob("*.jpg"):
            if f.name not in used_photos:
                f.unlink()

    tours.sort(key=lambda t: (t.get("departure") or "9999", t["priceFrom"]))
    if old_file.get("tours") != tours:
        save_json(OUTPUT, {"updatedAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                           "tours": tours})
        print(f"Авторские туры: файл обновлён, туров {len(tours)}")
    else:
        print(f"Авторские туры: без изменений, туров {len(tours)}")
    if problems:
        print(f"Авторские туры: постов с ошибками {problems}")

    alive = {t["id"] for t in tours}
    state = {"reported": {k: v for k, v in reported.items() if any(p.text_msg.id == int(k) for p in posts)},
             "published": [i for i in published if i in alive]}
    if load_json(STATE, None) != state:
        save_json(STATE, state)


def price_text(tour: dict) -> str:
    value = f"{tour['priceFrom']:,}".replace(",", " ")
    cur = tour.get("currency") or "₸"
    return f"${value}" if cur == "$" else f"{value} {cur}"
