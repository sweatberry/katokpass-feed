"""Бот для агентств-партнёров: горящие рейсы и горящие туры.

Рейс: агентство присылает обычное сообщение в том же виде, что и в канале.
Тур: команда /tur — бот задаёт вопросы по шагам и в конце показывает карточку.

Кто может публиковать, задаётся в agencies.json. Партнёр с "trusted": true
публикует сразу, новый — через вашу проверку: карточка уходит вам с кнопками
«Опубликовать» и «Отклонить».

Заявки по турам идут напрямую в агентство, поэтому у каждого партнёра
в agencies.json указан свой номер WhatsApp.

Запускается тем же расписанием, что и сбор, перед collect.py. Сервер не нужен.

Секреты (GitHub → Settings → Secrets and variables → Actions):
  TG_BOT_TOKEN — токен бота от @BotFather
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tours as tours_store  # noqa: E402
from post_parser import parse_post  # noqa: E402
from tours import Tour, meals_code, parse_date, parse_int, parse_price  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "sources.json"
AGENCIES = ROOT / "agencies.json"
STATE = ROOT / "docs" / "bot_state.json"
PHOTOS = ROOT / "docs" / "photos"

API = "https://api.telegram.org/bot{token}/{method}"
FILES = "https://api.telegram.org/file/bot{token}/{path}"
TIMEOUT = 25

HELP = (
    "Здравствуйте! Это бот KatokPass для агентств-партнёров.\n\n"
    "🎟 <b>Горящий рейс</b> — пришлите сообщение в таком виде:\n\n"
    "Алматы - Анталья\n"
    "S 22.09 - 89000 (4)\n"
    "S 22.09 - 29.09 = 165000\n\n"
    "S - SCAT\n"
    "Багаж 20 + 5 кг\n\n"
    "🏝 <b>Горящий тур</b> — отправьте команду /tur, и я задам несколько вопросов: "
    "страна, курорт, отель, даты, питание, цена и фото.\n\n"
    "Цены на рейсы указывайте закупочные — наценку приложение добавит само. "
    "Цену тура указывайте итоговую, за двоих целиком.\n\n"
    "/otmena — прервать заполнение тура."
)

FORMAT_ERROR = (
    "Не удалось разобрать рейс 😕\n\n"
    "Проверьте, что в первой строке маршрут (оба города), а ниже — дата и цена:\n\n"
    "Алматы - Анталья\n"
    "S 22.09 - 89000 (4)\n\n"
    "S - SCAT\n"
    "Багаж 20 + 5 кг\n\n"
    "Если хотите добавить тур, отправьте /tur"
)

# Шаги мастера: поле, вопрос, как проверяем ответ.
STEPS = [
    ("country", "1/10 · В какую страну тур?", "text"),
    ("resort", "2/10 · Курорт или город?", "text"),
    ("hotel", "3/10 · Название отеля?", "text"),
    ("stars", "4/10 · Сколько звёзд у отеля? Цифрой от 1 до 5", "stars"),
    ("from", "5/10 · Город вылета?", "text"),
    ("departure", "6/10 · Дата вылета, например 22.10", "date"),
    ("nights", "7/10 · Сколько ночей?", "nights"),
    ("meals", "8/10 · Питание: AI, UAI, FB, HB, BB или RO", "meals"),
    ("price", "9/10 · Цена за двоих целиком, в тенге", "price"),
    ("photo", "10/10 · Пришлите фото отеля одной картинкой", "photo"),
]


def api(token: str, method: str, **params) -> dict | list:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}
    ).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Bot API {method}: HTTP {e.code} {e.read()[:200]!r}")
        return {}
    except Exception as e:  # noqa: BLE001 — сеть может моргнуть
        print(f"Bot API {method}: {e}")
        return {}
    if not payload.get("ok"):
        print(f"Bot API {method}: {payload.get('description')}")
        return {}
    return payload.get("result") or {}


def say(token: str, chat, text: str, keyboard: list | None = None) -> None:
    api(token, "sendMessage", chat_id=chat, text=text, parse_mode="HTML",
        disable_web_page_preview="true",
        reply_markup=json.dumps({"inline_keyboard": keyboard}) if keyboard else None)


def download_photo(token: str, file_id: str) -> str | None:
    """Скачивает фото в docs/photos и возвращает путь вида photos/abc.jpg."""
    info = api(token, "getFile", file_id=file_id)
    path = (info or {}).get("file_path") if isinstance(info, dict) else None
    if not path:
        return None
    name = f"{uuid.uuid4().hex}{Path(path).suffix or '.jpg'}"
    PHOTOS.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(FILES.format(token=token, path=path), timeout=TIMEOUT) as resp:
            (PHOTOS / name).write_bytes(resp.read())
    except Exception as e:  # noqa: BLE001
        print(f"Не удалось скачать фото: {e}")
        return None
    return f"photos/{name}"


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", "utf-8")


def agencies_by_chat() -> dict[str, dict]:
    raw = load_json(AGENCIES, {"agencies": []})
    return {str(a.get("chatId") or "").strip(): a
            for a in raw.get("agencies", []) if a.get("chatId")}


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₸"


def short_date(iso: str) -> str:
    parts = iso.split("-")
    return f"{parts[2]}.{parts[1]}" if len(parts) == 3 else iso


def flight_summary(deals) -> str:
    lines = []
    for d in deals[:10]:
        when = short_date(d.departure)
        if d.returnDate:
            when += f" → {short_date(d.returnDate)}"
        lines.append(f"• {d.origin} → {d.destination}, {when}, {money(d.price)}")
    if len(deals) > 10:
        lines.append(f"…и ещё {len(deals) - 10}")
    return "\n".join(lines)


def check(kind: str, value: str, today: date):
    """Проверяет ответ на шаге мастера. Возвращает (значение, ошибка)."""
    value = (value or "").strip()
    if kind == "text":
        return (value, None) if 2 <= len(value) <= 60 else (None, "Напишите от 2 до 60 символов.")
    if kind == "stars":
        stars = parse_int(value, 1, 5)
        return (stars, None) if stars else (None, "Нужна цифра от 1 до 5.")
    if kind == "nights":
        nights = parse_int(value, 1, 30)
        return (nights, None) if nights else (None, "Нужно число ночей от 1 до 30.")
    if kind == "date":
        when = parse_date(value, today)
        if not when:
            return None, "Не понял дату. Напишите как 22.10"
        if when < today:
            return None, "Эта дата уже прошла."
        return when.isoformat(), None
    if kind == "meals":
        meals = meals_code(value)
        return (meals, None) if meals else (None, "Напишите AI, UAI, FB, HB, BB или RO.")
    if kind == "price":
        price = parse_price(value)
        return (price, None) if price else (None, "Цена должна быть от 30 000 ₸ за двоих.")
    return value, None


def build_tour(draft: dict, agency: dict) -> Tour:
    meals, label = draft["meals"]
    return Tour(
        id=uuid.uuid4().hex[:12],
        country=draft["country"], resort=draft["resort"], hotel=draft["hotel"],
        stars=int(draft["stars"]), from_=draft["from"], departure=draft["departure"],
        nights=int(draft["nights"]), meals=meals, mealsLabel=label,
        price=int(draft["price"]), photo=draft.get("photo"),
        agency=agency.get("name") or "Агентство",
        whatsApp="".join(ch for ch in str(agency.get("whatsApp") or "") if ch.isdigit()),
    )


def publish(tour: Tour, ttl_days: float, settings: dict) -> None:
    tours_store.save(tours_store.add(tour, ttl_days), settings)


def main() -> None:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        print("TG_BOT_TOKEN не задан — бот пропущен")
        return

    config = load_json(CONFIG, {})
    channel = str(config.get("botChannel") or "").strip()
    admin = str(config.get("adminChat") or "").strip()
    ttl = timedelta(hours=float(config.get("ttlHours") or 24))
    tour_ttl = float(config.get("toursTtlDays") or tours_store.DEFAULT_TTL_DAYS)
    tour_settings = {"ttlDays": tour_ttl}

    agencies = agencies_by_chat()
    state = load_json(STATE, {})
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    offset = int(state.get("offset") or 0)
    stats: dict = state.get("stats") or {}
    drafts: dict = state.get("drafts") or {}
    pending: dict = state.get("pending") or {}
    today = datetime.now(timezone.utc).date()

    updates = api(token, "getUpdates", offset=offset or None, timeout=0,
                  allowed_updates=json.dumps(["message", "callback_query"]))
    if not isinstance(updates, list):
        updates = []

    seen = published_flights = published_tours = 0

    for update in updates:
        offset = max(offset, int(update.get("update_id", 0)) + 1)

        # ── кнопки модерации ────────────────────────────────────────────
        callback = update.get("callback_query")
        if callback:
            data = str(callback.get("data") or "")
            chat = str(((callback.get("message") or {}).get("chat") or {}).get("id") or "")
            action, _, tour_id = data.partition(":")
            item = pending.pop(tour_id, None)
            api(token, "answerCallbackQuery", callback_query_id=callback.get("id"),
                text="Готово" if item else "Эта заявка уже обработана")
            if not item:
                continue
            if action == "ok":
                tour = Tour.from_json(item["tour"])
                publish(tour, tour_ttl, tour_settings)
                published_tours += 1
                name = tour.agency
                stats[name] = {**stats.get(name, {}), "tours": int((stats.get(name) or {}).get("tours", 0)) + 1}
                say(token, chat, f"Опубликовано ✅\n\n{tour.summary()}")
                say(token, item["chat"], f"Ваш тур опубликован ✅\n\n{tour.summary()}")
            else:
                photo = (item.get("tour") or {}).get("photo")
                if photo:  # лишнее фото в репозитории не держим
                    (ROOT / "docs" / photo).unlink(missing_ok=True)
                say(token, chat, "Отклонено ❌")
                say(token, item["chat"],
                    "Тур не прошёл проверку 😕 Свяжитесь с нами, подскажем, что поправить.")
            continue

        message = update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id") or "")
        text = (message.get("text") or "").strip()
        photos = message.get("photo") or []
        if not chat or (not text and not photos):
            continue
        seen += 1

        if text.startswith("/start") or text.startswith("/help"):
            say(token, chat, HELP)
            continue
        if text.startswith("/id"):
            say(token, chat, f"Номер этого чата: <b>{chat}</b>")
            continue

        agency = agencies.get(chat)
        if not agency or not agency.get("enabled", True):
            say(token, chat, "Пока у вас нет доступа к публикации.\n\n"
                             f"Передайте этот номер администратору KatokPass: <b>{chat}</b>")
            continue
        name = agency.get("name") or "Агентство"

        if text.startswith("/otmena") or text.startswith("/cancel"):
            drafts.pop(chat, None)
            say(token, chat, "Заполнение тура отменено.")
            continue

        # ── мастер тура ─────────────────────────────────────────────────
        if text.startswith("/tur") or text.startswith("/tour"):
            drafts[chat] = {"step": 0}
            say(token, chat, "Добавляем горящий тур 🏝\n\n" + STEPS[0][1] +
                             "\n\nПрервать — /otmena")
            continue

        draft = drafts.get(chat)
        if draft is not None:
            step = int(draft.get("step", 0))
            field, question, kind = STEPS[step]

            if kind == "photo":
                if not photos:
                    say(token, chat, "Нужна картинка. Пришлите фото отеля одним снимком.")
                    continue
                best = max(photos, key=lambda p: int(p.get("file_size") or 0))
                saved = download_photo(token, best.get("file_id", ""))
                if not saved:
                    say(token, chat, "Не получилось сохранить фото, пришлите ещё раз.")
                    continue
                draft["photo"] = saved
                draft["photoFileId"] = best.get("file_id", "")
            else:
                value, error = check(kind, text, today)
                if error:
                    say(token, chat, error)
                    continue
                draft[field] = value

            step += 1
            if step < len(STEPS):
                draft["step"] = step
                drafts[chat] = draft
                say(token, chat, STEPS[step][1])
                continue

            # все шаги пройдены
            drafts.pop(chat, None)
            tour = build_tour(draft, agency)
            if agency.get("trusted"):
                publish(tour, tour_ttl, tour_settings)
                published_tours += 1
                stats[name] = {**stats.get(name, {}),
                               "tours": int((stats.get(name) or {}).get("tours", 0)) + 1}
                say(token, chat, f"Тур опубликован ✅\n\n{tour.summary()}\n\n"
                                 "В приложении появится в течение 15 минут.")
            elif admin:
                item = tour.to_json()
                pending[tour.id] = {"tour": item, "chat": chat}
                # Фото пересылаем по file_id: на GitHub оно попадёт только после коммита.
                if draft.get("photoFileId"):
                    api(token, "sendPhoto", chat_id=admin, photo=draft["photoFileId"])
                keyboard = [[{"text": "✅ Опубликовать", "callback_data": f"ok:{tour.id}"},
                             {"text": "❌ Отклонить", "callback_data": f"no:{tour.id}"}]]
                say(token, admin, f"Новый тур от «{name}»\n\n{tour.summary()}", keyboard)
                say(token, chat, f"Тур отправлен на проверку ⏳\n\n{tour.summary()}\n\n"
                                 "Обычно отвечаем в течение часа.")
            else:
                publish(tour, tour_ttl, tour_settings)
                published_tours += 1
                say(token, chat, f"Тур опубликован ✅\n\n{tour.summary()}")
            continue

        # ── обычное сообщение: рейс ─────────────────────────────────────
        posted = datetime.now(timezone.utc)
        deals = parse_post(text, int(message.get("message_id", 0)), posted, name, ttl)
        if not deals:
            say(token, chat, FORMAT_ERROR)
            continue
        if not channel:
            say(token, chat, "Канал для публикации не настроен, сообщите администратору.")
            continue
        if not api(token, "sendMessage", chat_id=channel, text=text):
            say(token, chat, "Не получилось опубликовать рейс, попробуйте через пару минут.")
            continue
        published_flights += len(deals)
        stats[name] = {**stats.get(name, {}),
                       "flights": int((stats.get(name) or {}).get("flights", 0)) + len(deals)}
        say(token, chat, f"Принято, рейсов: {len(deals)} ✅\n\n{flight_summary(deals)}\n\n"
                         "В приложении появится в течение 15 минут и будет висеть 24 часа.")

    # чистим просроченные туры даже без новых сообщений
    tours_store.save(tours_store.load(), tour_settings)

    # Файл состояния пишем только при изменениях — иначе каждые 15 минут был бы лишний коммит.
    fresh = {**state, "offset": offset, "stats": stats, "drafts": drafts, "pending": pending}
    if json.dumps(fresh, ensure_ascii=False, sort_keys=True) != before or not STATE.exists():
        save_json(STATE, fresh)
    print(f"Бот: сообщений {seen}, рейсов {published_flights}, туров {published_tours}")


if __name__ == "__main__":
    main()
