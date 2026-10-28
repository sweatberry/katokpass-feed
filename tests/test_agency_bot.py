"""Проверка бота без сети: подменяем Telegram API заранее записанным разговором."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collector"))

import agency_bot as bot  # noqa: E402
import tours as tours_store  # noqa: E402

# Песочницу держим в системной временной папке, чтобы не мусорить в проекте.
SANDBOX = Path(tempfile.gettempdir()) / "katokpass-bot-test"
AGENCY_CHAT = "555111"
NEW_CHAT = "555222"
ADMIN = "999000"

sent: list[tuple] = []
updates_queue: list[list[dict]] = []
update_id = [100]


def message(chat: str, text: str | None = None, photo: bool = False) -> dict:
    update_id[0] += 1
    msg: dict = {"message_id": update_id[0], "chat": {"id": int(chat)}}
    if text is not None:
        msg["text"] = text
    if photo:
        msg["photo"] = [{"file_id": "small", "file_size": 100},
                        {"file_id": "big", "file_size": 9000}]
    return {"update_id": update_id[0], "message": msg}


def callback(data: str, chat: str = ADMIN) -> dict:
    update_id[0] += 1
    return {"update_id": update_id[0],
            "callback_query": {"id": "cb", "data": data,
                               "message": {"chat": {"id": int(chat)}}}}


def fake_api(token, method, **params):
    if method == "getUpdates":
        return updates_queue.pop(0) if updates_queue else []
    if method == "getFile":
        return {"file_path": "photos/file_1.jpg"}
    sent.append((method, str(params.get("chat_id") or ""), params))
    return {"message_id": 1}


def fake_download(token, file_id):
    bot.PHOTOS.mkdir(parents=True, exist_ok=True)
    name = f"{file_id}.jpg"
    (bot.PHOTOS / name).write_bytes(b"jpeg")
    return f"photos/{name}"


def setup() -> None:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    (SANDBOX / "docs").mkdir(parents=True, exist_ok=True)
    (SANDBOX / "sources.json").write_text(json.dumps({
        "whatsApp": "77755599977", "ttlHours": 24, "markupPercent": 3,
        "botChannel": "-1001234567890", "adminChat": ADMIN, "toursTtlDays": 5,
        "sources": [{"name": "Свой канал", "telegram": "-1001234567890"}],
    }), "utf-8")
    (SANDBOX / "agencies.json").write_text(json.dumps({"agencies": [
        {"name": "Проверенное АО", "chatId": AGENCY_CHAT, "enabled": True,
         "trusted": True, "whatsApp": "77011112233"},
        {"name": "Новое агентство", "chatId": NEW_CHAT, "enabled": True,
         "trusted": False, "whatsApp": "77022223344"},
    ]}), "utf-8")

    bot.ROOT = SANDBOX
    bot.CONFIG = SANDBOX / "sources.json"
    bot.AGENCIES = SANDBOX / "agencies.json"
    bot.STATE = SANDBOX / "docs" / "bot_state.json"
    bot.PHOTOS = SANDBOX / "docs" / "photos"
    tours_store.OUTPUT = SANDBOX / "docs" / "tours.json"
    bot.api = fake_api
    bot.download_photo = fake_download


def run(updates: list[dict]) -> None:
    sent.clear()
    updates_queue.append(updates)
    bot.main()


def to(chat: str) -> list[str]:
    return [p.get("text", "") for m, c, p in sent if c == chat and m == "sendMessage"]


def tours_now() -> list[dict]:
    return tours_store.load(tours_store.OUTPUT)


passed = [0]


def check(name: str, ok: bool, extra: str = "") -> None:
    if not ok:
        raise AssertionError(f"{name} — {extra}" if extra else name)
    passed[0] += 1


def main() -> None:
    import os
    os.environ["TG_BOT_TOKEN"] = "test-token"
    setup()

    # 1. Незнакомый чат получает отказ и свой номер
    run([message("777999", "Привет")])
    check("незнакомый чат", any("777999" in t for t in to("777999")))

    # 2. Проверенное агентство: тур публикуется сразу
    answers = ["Турция", "Анталья", "Sea Life Resort", "5", "Алматы",
               "22.10", "7", "ультра", "560000"]
    run([message(AGENCY_CHAT, "/tur")] +
        [message(AGENCY_CHAT, a) for a in answers] +
        [message(AGENCY_CHAT, photo=True)])
    tours = tours_now()
    check("тур опубликован сразу", len(tours) == 1, str(tours))
    t = tours[0]
    check("цена за двоих", t["price"] == 560000)
    check("питание UAI", (t["meals"], t["mealsLabel"]) == ("UAI", "ультра всё включено"))
    check("дата вылета", t["departure"].endswith("-10-22"))
    check("ночей", t["nights"] == 7)
    check("звёзды", t["stars"] == 5)
    check("город вылета", t["from"] == "Алматы")
    check("свой WhatsApp", t["whatsApp"] == "77011112233")
    check("что входит", t["includes"][:2] == ["Перелёт", "Проживание"])
    check("фото сохранено", (SANDBOX / "docs" / t["photo"]).exists(), str(t["photo"]))
    check("агентству ответили", any("опубликован" in x for x in to(AGENCY_CHAT)))
    check("модерация не нужна", not to(ADMIN))

    # 3. Ошибки в ответах не ломают мастер
    run([message(AGENCY_CHAT, "/tur"), message(AGENCY_CHAT, "Египет"),
         message(AGENCY_CHAT, "Хургада"), message(AGENCY_CHAT, "Albatros"),
         message(AGENCY_CHAT, "восемь"),        # звёзды — ошибка
         message(AGENCY_CHAT, "5"),
         message(AGENCY_CHAT, "Астана"),
         message(AGENCY_CHAT, "вчера"),         # дата — ошибка
         message(AGENCY_CHAT, "01.01.2020"),    # прошедшая дата
         message(AGENCY_CHAT, "15.11"),
         message(AGENCY_CHAT, "10"),
         message(AGENCY_CHAT, "AI"),
         message(AGENCY_CHAT, "5000"),          # цена — ошибка
         message(AGENCY_CHAT, "720 000"),
         message(AGENCY_CHAT, photo=True)])
    replies = to(AGENCY_CHAT)
    check("ошибка звёзд", any("цифра от 1 до 5" in x for x in replies))
    check("ошибка даты", any("Не понял дату" in x for x in replies))
    check("прошедшая дата", any("уже прошла" in x for x in replies))
    check("ошибка цены", any("30 000" in x for x in replies))
    check("второй тур добавлен", len(tours_now()) == 2, str(len(tours_now())))
    check("цена с пробелом", any(t["price"] == 720000 for t in tours_now()))

    # 4. Новое агентство: тур уходит на проверку, в файл не попадает
    run([message(NEW_CHAT, "/tur")] +
        [message(NEW_CHAT, a) for a in
         ["Вьетнам", "Нячанг", "Muong Thanh", "4", "Алматы", "05.12", "9", "BB", "480000"]] +
        [message(NEW_CHAT, photo=True)])
    check("на проверке, не в файле", len(tours_now()) == 2)
    check("агентству сказали про проверку", any("на проверку" in x for x in to(NEW_CHAT)))
    admin_msgs = to(ADMIN)
    check("админу пришла карточка", any("Новое агентство" in x for x in admin_msgs), str(admin_msgs))
    check("админу пришло фото", any(m == "sendPhoto" and c == ADMIN for m, c, p in sent))
    keyboard = [p for m, c, p in sent if c == ADMIN and p.get("reply_markup")]
    check("есть кнопки", bool(keyboard))
    tour_id = json.loads(keyboard[0]["reply_markup"])["inline_keyboard"][0][0]["callback_data"].split(":")[1]

    state = json.loads((SANDBOX / "docs" / "bot_state.json").read_text("utf-8"))
    check("заявка сохранена", tour_id in state["pending"])
    check("черновики пусты", not state["drafts"], str(state["drafts"]))

    # 5. Кнопка «Опубликовать»
    run([callback(f"ok:{tour_id}")])
    tours = tours_now()
    check("после одобрения 3 тура", len(tours) == 3, str(len(tours)))
    approved = [t for t in tours if t["id"] == tour_id]
    check("одобренный тур на месте", len(approved) == 1)
    check("WhatsApp нового агентства", approved and approved[0]["whatsApp"] == "77022223344")
    check("агентство узнало", any("опубликован" in x for x in to(NEW_CHAT)))

    # 6. Повторное нажатие той же кнопки ничего не делает
    run([callback(f"ok:{tour_id}")])
    check("повтор не дублирует", len(tours_now()) == 3)

    # 7. Отклонение: тур не публикуется, фото удаляется
    run([message(NEW_CHAT, "/tur")] +
        [message(NEW_CHAT, a) for a in
         ["ОАЭ", "Дубай", "Rove Hotel", "3", "Алматы", "20.12", "5", "RO", "390000"]] +
        [message(NEW_CHAT, photo=True)])
    keyboard = [p for m, c, p in sent if c == ADMIN and p.get("reply_markup")]
    bad_id = json.loads(keyboard[0]["reply_markup"])["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    run([callback(f"no:{bad_id}")])
    check("отклонённый не опубликован", all(t["id"] != bad_id for t in tours_now()))
    check("агентству сказали об отказе", any("не прошёл" in x for x in to(NEW_CHAT)))

    # 8. /otmena сбрасывает черновик
    run([message(AGENCY_CHAT, "/tur"), message(AGENCY_CHAT, "Таиланд"),
         message(AGENCY_CHAT, "/otmena")])
    state = json.loads((SANDBOX / "docs" / "bot_state.json").read_text("utf-8"))
    check("черновик отменён", AGENCY_CHAT not in state["drafts"])

    # 9. Рейс по-прежнему работает
    # Дату берём от сегодняшнего дня: рейс с прошедшей датой сборщик справедливо отвергает.
    soon = (datetime.now(timezone.utc) + timedelta(days=25)).strftime("%d.%m")
    run([message(AGENCY_CHAT, f"Алматы - Анталья\nS {soon} - 89000 (4)\n\nS - SCAT\nБагаж 20 + 5 кг")])
    check("рейс принят", any("Принято, рейсов" in x for x in to(AGENCY_CHAT)), str(to(AGENCY_CHAT)))
    check("рейс ушёл в канал", any(c == "-1001234567890" for m, c, p in sent))
    check("туры не тронуты", len(tours_now()) == 3)

    # 10. Непонятное сообщение — подсказка
    run([message(AGENCY_CHAT, "Здравствуйте, есть места?")])
    check("подсказка по формату", any("Не удалось разобрать" in x for x in to(AGENCY_CHAT)))

    # 11. Статистика по агентствам
    state = json.loads((SANDBOX / "docs" / "bot_state.json").read_text("utf-8"))
    check("статистика туров", state["stats"]["Проверенное АО"]["tours"] == 2, str(state["stats"]))
    check("статистика рейсов", state["stats"]["Проверенное АО"]["flights"] >= 1, str(state["stats"]))

    # 12. Файл tours.json отсортирован по дате
    dates = [t["departure"] for t in tours_now()]
    check("сортировка по дате", dates == sorted(dates), str(dates))

    print(f"\nПроверок пройдено: {passed[0]}")
    shutil.rmtree(SANDBOX, ignore_errors=True)


class BotTests(unittest.TestCase):
    """Один прогон: разговор с ботом от начала до конца."""

    def test_full_dialogue(self):
        main()


if __name__ == "__main__":
    main()
