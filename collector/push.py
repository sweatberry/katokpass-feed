"""Пуш-уведомления через Firebase Cloud Messaging.

Запускается после collect.py: сравнивает свежие docs/deals.json и docs/signature.json
с тем, о чём уже рассказывали (docs/push_state.json), и рассылает пуши.

Как устроена рассылка. Сервер не знает людей — только «темы» (topics).
Приложение само подписывается на темы по своим направлениям:

  рейсы:  "f_" + sha256("Турция|Алматы|120000|ru|i")[:24]
          страна прилёта | город вылета или * | порог цены | язык | режим (i — сразу, e — сводка в 19:00)
  туры:   "tours_ru", "tours_kk" — только у тех, кто включил анонсы авторских туров
  избранный тур: "st_" + sha256("<id тура>|ru")[:24] — у тех, кто отметил тур сердечком
          и согласился получать новости по избранным турам. Пуш — когда мест осталось
          5 или меньше (и стало меньше) или когда снизилась цена. Не чаще раза в день на тур.

Рейс за 89 000 ₸ уходит во все темы с порогом от 90 000 ₸ и выше.
Порог в приложении выбирается из тех же ступеней, что в BUCKETS.

Правила — те же, что в приложении (NotificationPolicy.swift):
  • с 22:00 до 9:00 по Алматы молчим, рейсы ждут утра;
  • в одну тему — не больше одного пуша о рейсе в день, остальное — в сводку в 19:00;
  • в режиме «сводка» сразу приходит только рейс дешевле порога вдвое;
  • первый запуск ничего не шлёт — только запоминает, что уже есть.

Секрет (GitHub → Settings → Secrets and variables → Actions):
  FCM_SERVICE_ACCOUNT — JSON-ключ сервисного аккаунта Firebase целиком.
Пока секрета нет, шаг просто пропускается.
"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
DEALS = ROOT / "docs" / "deals.json"
SIGNATURE = ROOT / "docs" / "signature.json"
STATE = ROOT / "docs" / "push_state.json"

ALMATY = timezone(timedelta(hours=5))
DAY_START, DAY_END, DIGEST_HOUR = 9, 22, 19

# Ступени порога цены. Такие же — в приложении (PushTopics.buckets).
BUCKETS = [20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000, 90_000, 100_000,
           120_000, 150_000, 200_000, 250_000, 300_000, 400_000, 500_000]
LANGS = ("ru", "kk")
MODES = ("i", "e")
TOUR_TOPIC = "tours_{lang}"
MAX_TOPICS_PER_CONDITION = 5   # ограничение FCM
# Папка docs в репозитории — отсюда iPhone скачивает фото тура для уведомления.
DOCS_BASE = "https://raw.githubusercontent.com/sweatberry/katokpass-feed/main/docs/"

# Флаги стран — те же, что Countries.flags в приложении. Нужны для картинки-карточки рейса.
FLAGS = {"Египет": "🇪🇬", "Турция": "🇹🇷", "ОАЭ": "🇦🇪", "Вьетнам": "🇻🇳", "Таиланд": "🇹🇭", "Индонезия": "🇮🇩", "Китай": "🇨🇳", "Южная Корея": "🇰🇷", "Япония": "🇯🇵", "Мальдивы": "🇲🇻", "Шри-Ланка": "🇱🇰", "Индия": "🇮🇳", "Сейшелы": "🇸🇨", "Танзания": "🇹🇿", "Грузия": "🇬🇪", "Азербайджан": "🇦🇿", "Армения": "🇦🇲", "Катар": "🇶🇦", "Израиль": "🇮🇱", "Саудовская Аравия": "🇸🇦", "Сербия": "🇷🇸", "Венгрия": "🇭🇺", "Германия": "🇩🇪", "Италия": "🇮🇹", "Черногория": "🇲🇪", "Испания": "🇪🇸", "Франция": "🇫🇷", "Чехия": "🇨🇿", "Австрия": "🇦🇹", "Греция": "🇬🇷", "Кипр": "🇨🇾", "Великобритания": "🇬🇧", "Кыргызстан": "🇰🇬", "Узбекистан": "🇺🇿", "Россия": "🇷🇺", "Казахстан": "🇰🇿", "Исландия": "🇮🇸", "Бразилия": "🇧🇷", "Аргентина": "🇦🇷", "Перу": "🇵🇪", "Чили": "🇨🇱", "Боливия": "🇧🇴", "Колумбия": "🇨🇴", "Мексика": "🇲🇽", "Куба": "🇨🇺", "Кения": "🇰🇪", "Марокко": "🇲🇦", "Эфиопия": "🇪🇹", "Мадагаскар": "🇲🇬", "ЮАР": "🇿🇦", "Намибия": "🇳🇦", "Иордания": "🇯🇴", "Оман": "🇴🇲", "Непал": "🇳🇵", "Бутан": "🇧🇹", "Монголия": "🇲🇳", "Филиппины": "🇵🇭", "Малайзия": "🇲🇾", "Сингапур": "🇸🇬", "Камбоджа": "🇰🇭", "Лаос": "🇱🇦", "Норвегия": "🇳🇴", "Швейцария": "🇨🇭", "Португалия": "🇵🇹", "Новая Зеландия": "🇳🇿", "Австралия": "🇦🇺", "Зимбабве": "🇿🇼", "Замбия": "🇿🇲", "Ботсвана": "🇧🇼", "США": "🇺🇸"}

# ── тексты ────────────────────────────────────────────────────────────────

MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
             "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_KK = ["қаңтар", "ақпан", "наурыз", "сәуір", "мамыр", "маусым", "шілде",
             "тамыз", "қыркүйек", "қазан", "қараша", "желтоқсан"]

# То же, что Places.kazakh в приложении.
KAZAKH = {
    "Египет": "Мысыр", "Турция": "Түркия", "ОАЭ": "БАӘ", "Китай": "Қытай",
    "Южная Корея": "Оңтүстік Корея", "Япония": "Жапония", "Мальдивы": "Мальдив аралдары",
    "Индия": "Үндістан", "Сейшелы": "Сейшел аралдары", "Азербайджан": "Әзірбайжан",
    "Саудовская Аравия": "Сауд Арабиясы", "Греция": "Грекия", "Великобритания": "Ұлыбритания",
    "Кыргызстан": "Қырғызстан", "Узбекистан": "Өзбекстан", "Россия": "Ресей",
    "Казахстан": "Қазақстан",
    "Караганда": "Қарағанды", "Актау": "Ақтау", "Актобе": "Ақтөбе",
    "Усть-Каменогорск": "Өскемен", "Костанай": "Қостанай", "Кызылорда": "Қызылорда",
    "Уральск": "Орал", "Туркестан": "Түркістан", "Петропавловск": "Петропавл",
    "Кокшетау": "Көкшетау", "Жезказган": "Жезқазған",
    "Анталия": "Анталья", "Москва": "Мәскеу", "Пекин": "Бейжің", "Абу-Даби": "Әбу-Даби",
}


def place(name: str, lang: str) -> str:
    return KAZAKH.get(name, name) if lang == "kk" else name


def day_text(iso: str, lang: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {(MONTHS_KK if lang == 'kk' else MONTHS_RU)[d.month - 1]}"


def money(value: int, currency: str | None = "₸") -> str:
    grouped = f"{value:,}".replace(",", " ")
    cur = (currency or "₸").strip() or "₸"
    return f"${grouped}" if cur == "$" else f"{grouped} {cur}"


def plural(n: int, one: str, few: str, many: str) -> str:
    h, t = n % 100, n % 10
    if 11 <= h <= 14:
        return many
    return one if t == 1 else few if 2 <= t <= 4 else many


def deal_message(deal: dict, lang: str) -> tuple[str, str]:
    """«Алматы → Анталия, 89 000 ₸» / «Вылет 22 октября, туда-обратно, SCAT.»"""
    title = f"{place(deal['from'], lang)} → {place(deal['to'], lang)}, {money(deal['price'])}"
    round_trip = deal.get("trip") == "roundTrip"
    airline = deal.get("airline") or ""
    if lang == "kk":
        trip = "барып-қайту" if round_trip else "бір жаққа"
        body = f"Ұшу {day_text(deal['departure'], lang)}, {trip}"
    else:
        trip = "туда-обратно" if round_trip else "в одну сторону"
        body = f"Вылет {day_text(deal['departure'], lang)}, {trip}"
    if airline:
        body += f", {airline}"
    body += "."
    seats = deal.get("seats")
    if seats:
        body += (f" {seats} орын қалды." if lang == "kk"
                 else f" Осталось {seats} {plural(seats, 'место', 'места', 'мест')}.")
    return title, body


def digest_message(deals: list[dict], lang: str) -> tuple[str, str]:
    """Сводка за день по одному направлению: сколько рейсов и самый дешёвый."""
    n = len(deals)
    best = min(deals, key=lambda d: (d["price"], d["departure"]))
    country = place(best.get("toCountry") or best["country"], lang)
    route = f"{place(best['from'], lang)} → {place(best['to'], lang)}"
    if lang == "kk":
        title = f"{country}: бүгін {n} жаңа рейс"
        body = f"Ең арзаны — {route}, {money(best['price'])}, ұшу {day_text(best['departure'], lang)}."
    else:
        title = f"{country}: {n} {plural(n, 'новый рейс', 'новых рейса', 'новых рейсов')} за день"
        body = f"Самый дешёвый — {route}, {money(best['price'])}, вылет {day_text(best['departure'], lang)}."
    return title, body


def tour_dates(tour: dict, lang: str) -> str:
    """Как в приложении: «с 12 по 19 ноября», год — только если он был в посте."""
    dep = tour.get("departure")
    if not dep:
        return tour.get("datesNote") or ("күндері сұрау бойынша" if lang == "kk" else "даты по запросу")
    d1 = date.fromisoformat(dep[:10])
    if tour.get("returnDate"):
        d2 = date.fromisoformat(tour["returnDate"][:10])
    elif tour.get("nights"):
        d2 = d1 + timedelta(days=int(tour["nights"]))
    else:
        d2 = None
    show_year = bool(tour.get("explicitYear"))
    months = MONTHS_KK if lang == "kk" else MONTHS_RU

    def long(d: date) -> str:
        return f"{d.day} {months[d.month - 1]}"

    if d2 is None:
        return long(d1) + (f" {d1.year}" if show_year else "")
    tail = f" {d2.year}" if show_year else ""
    if show_year and d1.year != d2.year:
        return (f"{long(d1)} {d1.year} – {long(d2)} {d2.year}" if lang == "kk"
                else f"с {long(d1)} {d1.year} по {long(d2)} {d2.year}")
    if (d1.year, d1.month) == (d2.year, d2.month):
        return f"{d1.day}–{long(d2)}{tail}" if lang == "kk" else f"с {d1.day} по {long(d2)}{tail}"
    return f"{long(d1)} – {long(d2)}{tail}" if lang == "kk" else f"с {long(d1)} по {long(d2)}{tail}"


def tour_image(tour: dict) -> str | None:
    """Обложка тура — её iPhone покажет в уведомлении крупно."""
    photos = tour.get("photos") or []
    return DOCS_BASE + photos[0].lstrip("/") if photos else None


def deal_card(deal: dict, lang: str) -> dict:
    """Данные для картинки-карточки рейса: её рисует сам iPhone (NotificationService)."""
    dates = day_text(deal["departure"], lang)
    if deal.get("returnDate"):
        dates += " → " + day_text(deal["returnDate"], lang)
    round_trip = deal.get("trip") == "roundTrip"
    trip = ("барып-қайту" if round_trip else "бір жаққа") if lang == "kk" else \
           ("туда-обратно" if round_trip else "в одну сторону")
    return {
        "cardFrom": place(deal["from"], lang),
        "cardTo": place(deal["to"], lang),
        "cardFlag": FLAGS.get(deal_country(deal), "✈️"),
        "cardPrice": money(deal["price"]),
        "cardPer": "бір адамға" if lang == "kk" else "на человека",
        "cardDates": f"{dates} · {trip}",
    }


def tour_message(tour: dict, lang: str) -> tuple[str, str]:
    flags = "".join(tour.get("flags") or [])
    head = "Жаңа авторлық тур" if lang == "kk" else "Новый авторский тур"
    title = f"{flags} {head}: {tour['title']}".strip()
    price = money(int(tour["priceFrom"]), tour.get("currency"))
    price_line = f"{price} бастап, бір адамға" if lang == "kk" else f"от {price} за человека"
    body = f"{tour_dates(tour, lang)} · {price_line}"
    seats = tour.get("seatsLeft")
    if seats:
        body += f" · {seats} орын қалды" if lang == "kk" else \
            f" · осталось {seats} {plural(seats, 'место', 'места', 'мест')}"
    return title, body


# ── темы ──────────────────────────────────────────────────────────────────

def flight_topic(country: str, origin: str | None, bucket: int, lang: str, mode: str) -> str:
    """Имя темы. Та же формула — в приложении (PushTopics.flight)."""
    key = f"{country}|{origin or '*'}|{bucket}|{lang}|{mode}"
    key = unicodedata.normalize("NFC", key)
    return "f_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def tour_topic(tour_id: str, lang: str) -> str:
    """Тема избранного тура. Та же формула — в приложении (PushTopics.favoriteTour)."""
    key = unicodedata.normalize("NFC", f"{tour_id}|{lang}")
    return "st_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


SEATS_ALERT = 5   # «осталось N мест» — когда мест 5 или меньше


def tour_snapshot(tour: dict) -> dict:
    seats = tour.get("seatsLeft")
    return {"seats": int(seats) if isinstance(seats, int) or (isinstance(seats, str) and seats.isdigit()) else None,
            "price": int(tour.get("priceFrom") or 0),
            "currency": tour.get("currency") or ""}


def favorite_message(tour: dict, kind: str, lang: str, old_price: int | None = None) -> tuple[str, str]:
    """Новость по избранному туру: мало мест или цена снизилась."""
    flags = "".join(tour.get("flags") or [])
    price = money(int(tour["priceFrom"]), tour.get("currency"))
    dates = tour_dates(tour, lang)
    if kind == "seats":
        n = int(tour["seatsLeft"])
        if lang == "kk":
            head, body = f"{n} орын қалды", f"{dates} · {price} бастап, бір адамға"
        else:
            head = f"Осталось {n} {plural(n, 'место', 'места', 'мест')}"
            body = f"{dates} · от {price} за человека"
    else:
        old = money(int(old_price or 0), tour.get("currency"))
        if lang == "kk":
            head, body = "Баға төмендеді", f"Енді {price} бастап ({old} орнына) · {dates}"
        else:
            head, body = "Цена снизилась", f"Теперь от {price} вместо {old} · {dates}"
    title = f"{flags} {head}: {tour['title']}".strip()
    return title, body


def deal_country(deal: dict) -> str:
    return deal.get("toCountry") or deal["country"]


def deal_targets(deal: dict):
    """Все темы, которым подходит рейс: (тема, язык, режим, порог)."""
    country = deal_country(deal)
    for origin in (deal["from"], None):
        for bucket in BUCKETS:
            if bucket < deal["price"]:
                continue
            for lang in LANGS:
                for mode in MODES:
                    yield flight_topic(country, origin, bucket, lang, mode), lang, mode, bucket


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ── отправка ─────────────────────────────────────────────────────────────

class Push:
    """Одно сообщение — в одну тему или в группу тем (условие FCM)."""

    def __init__(self, topics: list[str], title: str, body: str, data: dict,
                 collapse: str, thread: str, image: str | None = None):
        self.topics, self.title, self.body = topics, title, body
        self.data, self.collapse, self.thread = data, collapse, thread
        self.image = image

    def fcm(self) -> dict:
        message: dict = {
            "notification": {"title": self.title, "body": self.body},
            "data": {k: str(v) for k, v in {**self.data, **({"image": self.image} if self.image else {})}.items()},
            "apns": {
                # Один и тот же рейс из двух тем человека заменит сам себя, а не придёт дважды.
                "headers": {"apns-collapse-id": self.collapse[:64], "apns-priority": "10"},
                # mutable-content: iPhone даёт приложению добавить картинку (фото тура, карточку рейса).
                "payload": {"aps": {"sound": "default", "thread-id": self.thread, "mutable-content": 1}},
            },
        }
        if self.image:
            message["apns"]["fcm_options"] = {"image": self.image}
        if len(self.topics) == 1:
            message["topic"] = self.topics[0]
        else:
            message["condition"] = " || ".join(f"'{t}' in topics" for t in self.topics)
        return {"message": message}


def grouped_pushes(topics: list[str], make: Callable[[list[str]], Push]) -> list[Push]:
    return [make(group) for group in chunks(sorted(topics), MAX_TOPICS_PER_CONDITION)]


# ── правила ──────────────────────────────────────────────────────────────

def plan(deals_doc: dict | None, signature_doc: dict | None, state: dict | None,
         now: datetime) -> tuple[list[Push], dict, list[str]]:
    """Решает, что отправить. Чистая функция: на вход файлы и состояние, на выход пуши.

    Возвращает (пуши, новое состояние, строки для лога)."""
    deals = [d for d in (deals_doc or {}).get("deals", []) if isinstance(d, dict) and "id" in d]
    tours = [t for t in (signature_doc or {}).get("tours", []) if isinstance(t, dict) and "id" in t]
    by_id = {d["id"]: d for d in deals}
    local = now.astimezone(ALMATY)
    today = local.date().isoformat()
    log: list[str] = []

    if state is None:
        # Первый запуск: всё, что уже в ленте, люди видят в приложении — пушить нечего.
        new_state = {"day": today, "seenDeals": sorted(by_id), "seenTours": sorted(t["id"] for t in tours),
                     "topics": {}, "tourSnap": {t["id"]: tour_snapshot(t) for t in tours}}
        log.append(f"Пуши: первый запуск — запомнил рейсов {len(deals)}, туров {len(tours)}, ничего не отправляю")
        return [], new_state, log

    seen = set(state.get("seenDeals") or []) & set(by_id)
    seen_tours = set(state.get("seenTours") or []) & {t["id"] for t in tours}
    topics: dict = state.get("topics") or {}
    if state.get("day") != today:
        topics = {}     # новый день — новые лимиты
    # Снимки туров для новостей по избранным: места и цена в прошлый раз.
    # Если снимков ещё нет (обновили сервер) — просто запоминаем, ничего не шлём.
    had_snaps = "tourSnap" in state
    snaps: dict = {k: dict(v) for k, v in (state.get("tourSnap") or {}).items()}

    pushes: list[Push] = []
    daytime = DAY_START <= local.hour < DAY_END
    if not daytime:
        log.append("Пуши: ночь в Алматы — ждём 9:00")
    else:
        # ── новые рейсы ──
        fresh = sorted((d for d in deals if d["id"] not in seen), key=lambda d: (d["price"], d["departure"]))
        for deal in fresh:
            instant: dict[str, list[str]] = {lang: [] for lang in LANGS}
            held = 0
            for topic, lang, mode, bucket in deal_targets(deal):
                t = topics.setdefault(topic, {})
                super_cheap = deal["price"] * 2 <= bucket
                if (mode == "e" and not super_cheap) or t.get("instant"):
                    if t.get("digest"):
                        continue    # сводка сегодня уже ушла — рейс останется в ленте
                    t.setdefault("held", []).append(deal["id"])
                    t["lang"] = lang
                    held += 1
                else:
                    t["instant"] = True
                    instant[lang].append(topic)
            for lang, group in instant.items():
                if not group:
                    continue
                title, body = deal_message(deal, lang)
                card = deal_card(deal, lang)
                pushes += grouped_pushes(group, lambda g, title=title, body=body, card=card: Push(
                    g, title, body, {"dealID": deal["id"], **card}, f"deal-{deal['id']}", deal_country(deal)))
            seen.add(deal["id"])
            log.append(f"Рейс {deal['from']} → {deal['to']} {deal['price']}: "
                       f"сразу в {sum(len(v) for v in instant.values())} тем, в сводку {held}")

        # ── вечерняя сводка ──
        if local.hour >= DIGEST_HOUR:
            bundles: dict[tuple, list[str]] = {}
            for topic, t in topics.items():
                if t.get("digest"):
                    continue
                ids = [i for i in dict.fromkeys(t.get("held") or []) if i in by_id]
                t["digest"] = True
                t["held"] = []
                if ids:
                    bundles.setdefault((t.get("lang", "ru"), tuple(ids)), []).append(topic)
            for (lang, ids), group in bundles.items():
                held_deals = [by_id[i] for i in ids]
                title, body = digest_message(held_deals, lang)
                best = min(held_deals, key=lambda d: d["price"])
                pushes += grouped_pushes(group, lambda g, title=title, body=body, best=best, lang=lang: Push(
                    g, title, body, {"dealID": best["id"], **deal_card(best, lang)},
                    f"digest-{today}-{g[0]}", "digest"))
            if bundles:
                log.append(f"Сводка: {sum(len(g) for g in bundles.values())} тем")

        # ── авторские туры: один анонс за запуск, остальные — через 15 минут ──
        for tour in tours:
            if tour["id"] in seen_tours:
                continue
            seen_tours.add(tour["id"])
            if tour.get("isSoldOut"):
                continue
            for lang in LANGS:
                title, body = tour_message(tour, lang)
                pushes.append(Push([TOUR_TOPIC.format(lang=lang)], title, body,
                                   {"signatureID": tour["id"]}, f"tour-{tour['id']}", "signature",
                                   image=tour_image(tour)))
            log.append(f"Анонс тура «{tour['title']}»")
            break

        # ── избранные туры: мало мест или цена снизилась ──
        for tour in tours:
            now_snap = tour_snapshot(tour)
            old = snaps.get(tour["id"])
            if not had_snaps or old is None or tour.get("isSoldOut"):
                snaps[tour["id"]] = {**now_snap, "alerted": (old or {}).get("alerted")}
                continue
            kind = None
            seats, old_seats = now_snap["seats"], old.get("seats")
            if seats is not None and 0 < seats <= SEATS_ALERT and (old_seats is None or seats < old_seats):
                kind = "seats"
            elif (now_snap["price"] and old.get("price") and now_snap["currency"] == old.get("currency")
                  and now_snap["price"] < old["price"]):
                kind = "price"
            if kind and old.get("alerted") == today:
                continue    # по этому туру сегодня уже писали — новость подождёт до завтра
            if kind:
                for lang in LANGS:
                    title, body = favorite_message(tour, kind, lang, old.get("price"))
                    pushes.append(Push([tour_topic(tour["id"], lang)], title, body,
                                       {"signatureID": tour["id"]}, f"fav-{tour['id']}", "signature",
                                       image=tour_image(tour)))
                log.append(f"Избранный тур «{tour['title']}»: "
                           + ("мало мест" if kind == "seats" else "цена снизилась"))
                snaps[tour["id"]] = {**now_snap, "alerted": today}
            else:
                snaps[tour["id"]] = {**now_snap, "alerted": old.get("alerted")}

    # Пустые записи тем не храним — файл остаётся маленьким.
    topics = {k: v for k, v in topics.items() if v.get("instant") or v.get("held") or v.get("digest")}
    alive = {t["id"] for t in tours}
    snaps = {k: v for k, v in snaps.items() if k in alive}
    new_state = {"day": today, "seenDeals": sorted(seen), "seenTours": sorted(seen_tours), "topics": topics,
                 "tourSnap": snaps}
    return pushes, new_state, log


# ── запуск ───────────────────────────────────────────────────────────────

def load(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


class FCM:
    """Отправка через FCM HTTP v1 с ключом сервисного аккаунта."""
    SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

    def __init__(self, info: dict):
        from google.oauth2 import service_account          # pip: google-auth
        from google.auth.transport.requests import AuthorizedSession
        creds = service_account.Credentials.from_service_account_info(info, scopes=[self.SCOPE])
        self.session = AuthorizedSession(creds)
        self.url = f"https://fcm.googleapis.com/v1/projects/{info['project_id']}/messages:send"

    def send(self, push: Push) -> tuple[bool, str]:
        try:
            r = self.session.post(self.url, json=push.fcm(), timeout=20)
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        return r.ok, "" if r.ok else f"{r.status_code} {r.text[:300]}"


# ── пробные уведомления ──────────────────────────────────────────────────
# Тема, на которую подписываются только сборки из Xcode (#if DEBUG в приложении).
# Настоящие пользователи из App Store эти уведомления никогда не получат.
TEST_TOPIC = "katokpass_test"


def test_pushes(kind: str, deals_doc: dict | None, signature_doc: dict | None) -> list[Push]:
    """Пробные уведомления — точь-в-точь как настоящие, только в тему TEST_TOPIC."""
    deals = [d for d in (deals_doc or {}).get("deals", []) if isinstance(d, dict) and "id" in d]
    tours = [t for t in (signature_doc or {}).get("tours", []) if isinstance(t, dict) and "id" in t]
    out: list[Push] = []
    if kind in ("tour", "all") and tours:
        t = tours[0]
        title, body = tour_message(t, "ru")
        out.append(Push([TEST_TOPIC], title, body, {"signatureID": t["id"]},
                        f"test-tour-{t['id']}", "signature", image=tour_image(t)))
    if kind in ("favorite", "all") and tours:
        t = {**tours[0], "seatsLeft": 3}
        title, body = favorite_message(t, "seats", "ru")
        out.append(Push([TEST_TOPIC], title, body, {"signatureID": t["id"]},
                        f"test-fav-{t['id']}", "signature", image=tour_image(t)))
    if kind in ("deal", "all") and deals:
        d = min(deals, key=lambda x: (x["price"], x["departure"]))
        title, body = deal_message(d, "ru")
        out.append(Push([TEST_TOPIC], title, body, {"dealID": d["id"], **deal_card(d, "ru")},
                        f"test-deal-{d['id']}", deal_country(d)))
    return out


def main() -> None:
    raw = os.environ.get("FCM_SERVICE_ACCOUNT", "").strip()
    if not raw:
        print("Пуши: секрет FCM_SERVICE_ACCOUNT не задан — пропускаю")
        return
    try:
        info = json.loads(raw)
        info["project_id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        print("Пуши: секрет FCM_SERVICE_ACCOUNT — не JSON-ключ сервисного аккаунта. "
              "Вставьте файл ключа целиком, от { до }.")
        return

    # Пробный запуск (GitHub → Actions → «Пробное уведомление»): шлём и выходим, состояние не трогаем.
    test = os.environ.get("PUSH_TEST", "").strip()
    if test:
        pushes = test_pushes(test, load(DEALS), load(SIGNATURE))
        if not pushes:
            print("Пробное уведомление: нет рейсов или туров для примера")
            return
        fcm = FCM(info)
        for p in pushes:
            good, err = fcm.send(p)
            print(f"Пробное «{p.title}»: " + ("отправлено" if good else f"ошибка {err}"))
        return

    pushes, state, log = plan(load(DEALS), load(SIGNATURE), load(STATE), datetime.now(timezone.utc))
    print("\n".join(log) or "Пуши: нового нет")

    if pushes:
        try:
            fcm = FCM(info)
        except Exception as e:  # noqa: BLE001
            print(f"Пуши: не удалось подключиться к Firebase — {e}")
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(fcm.send, pushes))
        ok = sum(1 for good, _ in results if good)
        errors = [err for good, err in results if not good]
        print(f"Пуши: отправлено {ok} из {len(pushes)}")
        for err in dict.fromkeys(errors):
            print(f"  ошибка: {err}")
        if ok == 0:
            # Firebase не принял ни одного — состояние не сохраняем, попробуем через 15 минут.
            # Ошибкой шаг не завершаем: иначе не сохранятся свежие рейсы.
            print("Пуши: Firebase не принял ни одного сообщения — проверьте ключ и проект")
            return

    if load(STATE) != state:
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", "utf-8")


if __name__ == "__main__":
    main()
