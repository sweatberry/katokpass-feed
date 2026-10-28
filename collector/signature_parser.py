"""Разбор поста об авторском туре — в том виде, в каком Sweatberry пишет его в Instagram.

Пост переписывать не нужно. Разборщик сам:
  • берёт название из первой строки;
  • берёт описание из первых свободных абзацев;
  • собирает маршрут из блоков «🇿🇦 Страна» + абзац;
  • находит варианты тура — блоки со строками «Даты:» и «Стоимость:»;
  • читает «Предоплата:», «В стоимость входят», «В стоимость не входят»;
  • вытаскивает номер WhatsApp — на него и пойдут заявки из приложения;
  • выбрасывает то, что нужно только в Instagram: «оставляй + в комментах»,
    «ссылка в шапке профиля», хештеги, ники.

Обязательно в посте только три вещи: название, даты и цена. Если чего-то нет,
parse() возвращает список ошибок — бот перешлёт его вам в Telegram.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

# ── страны по флажкам ─────────────────────────────────────────────────────────
COUNTRIES = {
    "ZA": "ЮАР", "ZW": "Зимбабве", "ZM": "Замбия", "NA": "Намибия", "BW": "Ботсвана",
    "KE": "Кения", "TZ": "Танзания", "MA": "Марокко", "EG": "Египет", "ET": "Эфиопия",
    "MG": "Мадагаскар", "IS": "Исландия", "NO": "Норвегия", "JP": "Япония", "KR": "Южная Корея",
    "CN": "Китай", "BR": "Бразилия", "AR": "Аргентина", "PE": "Перу", "CL": "Чили",
    "BO": "Боливия", "CO": "Колумбия", "MX": "Мексика", "CU": "Куба", "US": "США",
    "JO": "Иордания", "OM": "Оман", "AE": "ОАЭ", "NP": "Непал", "BT": "Бутан",
    "MN": "Монголия", "IN": "Индия", "LK": "Шри-Ланка", "MV": "Мальдивы", "TH": "Таиланд",
    "VN": "Вьетнам", "ID": "Индонезия", "PH": "Филиппины", "MY": "Малайзия",
    "SG": "Сингапур", "KH": "Камбоджа", "LA": "Лаос", "TR": "Турция", "GE": "Грузия",
    "AM": "Армения", "AZ": "Азербайджан", "UZ": "Узбекистан", "KG": "Кыргызстан",
    "IT": "Италия", "ES": "Испания", "PT": "Португалия", "FR": "Франция", "CH": "Швейцария",
    "GR": "Греция", "NZ": "Новая Зеландия", "AU": "Австралия", "KZ": "Казахстан",
}

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
MONTH_RE = r"(январ[ья]|феврал[ья]|марта?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|августа?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])"

FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
PHONE_RE = re.compile(r"(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")

# Строки, которые нужны только в Instagram, — в приложение не попадают.
NOISE = re.compile(
    r"(в\s+коммент|оставля[йе]|ссылк\w*\s+в\s+шапке|шапке\s+профиля|для\s+бронирования|"
    r"пишите\s+на\s+наш|подписывай|сохраняй|репост|"
    r"^(telegram|телеграм|instagram|инстаграм|whatsapp|ватсап)\s*:)", re.I)

LABELS = {
    "dates": r"даты?|когда|вылет",
    "price": r"стоимость|цена(?:\s+от)?|от",
    "prepay": r"предоплата|депозит|бронь",
    "group": r"группа|размер\s+группы",
    "seats": r"мест(?:а)?|осталось\s+мест",
    "conditions": r"условия",
    "includes": r"в\s+стоимость(?:\s+тура)?\s+вход\w*|стоимость(?:\s+тура)?\s+включает|включено|входит(?:\s+в\s+стоимость)?|что\s+входит",
    "excludes": r"в\s+стоимость(?:\s+тура)?\s+не\s+вход\w*|стоимость(?:\s+тура)?\s+не\s+включает|не\s+включено|не\s+входит(?:\s+в\s+стоимость)?|что\s+не\s+входит|оплачивается\s+отдельно|дополнительно\s+оплачивается",
    "highlights": r"что\s+увидим|что\s+нас\s+ждёт|что\s+нас\s+ждет",
    "programme": r"программа|маршрут",
}


def flag_to_iso(flag: str) -> str:
    return "".join(chr(ord(ch) - 0x1F1E6 + ord("A")) for ch in flag)


def strip_decor(line: str) -> str:
    """Убирает эмодзи и значки в начале строки: «🗓️ Даты:» → «Даты:»."""
    i = 0
    while i < len(line):
        ch = line[i]
        cat = unicodedata.category(ch)
        if ch.isalnum() or ch in "+-—–•·$€₸(«\"" or (cat.startswith("L") or cat.startswith("N")):
            break
        i += 1
    return line[i:].strip()


def clean_tail(text: str) -> str:
    """Убирает «👇🏻» и подобные указатели в конце — в приложении им некуда указывать."""
    return re.sub(r"[\s👇👆⬇️⤵️👉🏻🏼🏽🏾🏿‍️]+$", "", text).strip()


def tidy_title(line: str) -> str:
    """«Африке!🦁» → «Африке! 🦁»: пробел перед эмодзи в конце, больше ничего не трогаем."""
    line = line.strip()
    i = len(line)
    while i > 0 and (unicodedata.category(line[i - 1]) in ("So", "Sk", "Mn", "Cf")
                     or line[i - 1] in "\ufe0f\u200d"):
        i -= 1
    if 0 < i < len(line) and not line[i - 1].isspace():
        line = line[:i] + " " + line[i:]
    return line


def label_of(line: str) -> tuple[str | None, str]:
    """Определяет метку строки. Возвращает (тип, значение после двоеточия)."""
    plain = strip_decor(line)
    for kind in ("excludes", "includes", "dates", "price", "prepay", "group", "seats",
                 "conditions", "highlights", "programme"):
        m = re.match(rf"^({LABELS[kind]})\s*:?\s*(.*)$", plain, re.I)
        if not m:
            continue
        value = m.group(2).strip()
        # «от» без двоеточия и цифр — это обычное слово, не метка
        if kind == "price" and m.group(1).lower() == "от" and not re.search(r"\d", value):
            continue
        # для простых меток двоеточие обязательно, иначе ловим обычные фразы
        if kind in ("dates", "price", "prepay", "group", "seats", "conditions") and ":" not in plain[:40]:
            continue
        return kind, value
    return None, plain


# ── даты ──────────────────────────────────────────────────────────────────────
def month_num(word: str) -> int:
    w = word.lower()
    for stem, n in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(stem):
            return n
    return 0


def nearest(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        d = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and d < today:
        try:
            d = date(today.year + 1, month, day)
        except ValueError:
            return None
    return d


def parse_dates(text: str, today: date) -> tuple[date | None, date | None]:
    t = text.lower().replace("—", "-").replace("–", "-")
    # «с 20 ноября по 2 декабря», «20 ноября - 2 декабря 2027»
    m = re.search(rf"(\d{{1,2}})\s+{MONTH_RE}\s*(?:по|-|до)\s*(\d{{1,2}})\s+{MONTH_RE}(?:\s+(\d{{4}}))?", t)
    if m:
        year = int(m.group(5)) if m.group(5) else None
        end = nearest(int(m.group(3)), month_num(m.group(4)), year, today)
        start = nearest(int(m.group(1)), month_num(m.group(2)), year, today)
        if start and end and start > end:          # конец года: 28 декабря – 8 января
            start = start.replace(year=start.year - 1) if year else start
            if start > end:
                end = end.replace(year=end.year + 1)
        return start, end
    # «с 20 по 28 ноября», «7-16 февраля 2027»
    m = re.search(rf"(\d{{1,2}})\s*(?:по|-|до)\s*(\d{{1,2}})\s+{MONTH_RE}(?:\s+(\d{{4}}))?", t)
    if m:
        year = int(m.group(4)) if m.group(4) else None
        month = month_num(m.group(3))
        return nearest(int(m.group(1)), month, year, today), nearest(int(m.group(2)), month, year, today)
    # «07.02-16.02.2027»
    m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*-\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", t)
    if m:
        y2 = m.group(6) or m.group(3)
        year = (int(y2) + 2000 if y2 and len(y2) == 2 else int(y2)) if y2 else None
        return (nearest(int(m.group(1)), int(m.group(2)), year, today),
                nearest(int(m.group(4)), int(m.group(5)), year, today))
    # одна дата: «20 ноября»
    m = re.search(rf"(\d{{1,2}})\s+{MONTH_RE}(?:\s+(\d{{4}}))?", t)
    if m:
        year = int(m.group(3)) if m.group(3) else None
        return nearest(int(m.group(1)), month_num(m.group(2)), year, today), None
    return None, None


def _currency_in(t: str) -> str | None:
    if "$" in t or "usd" in t or "долл" in t:
        return "$"
    if "€" in t or "eur" in t or "евро" in t:
        return "€"
    if "₸" in t or "тг" in t or "тенге" in t or "kzt" in t:
        return "₸"
    return None


def parse_money(text: str) -> tuple[int | None, str]:
    """«1 690 000 ₸», «$4 000», «500 000 ₸ | $1 000» → первая сумма и её валюта."""
    t = (text or "").lower()
    m = re.search(r"\d[\d\s.,]*\d|\d", t)
    if not m:
        return None, _currency_in(t) or "₸"
    # валюта, стоящая вплотную к сумме: сначала сразу после неё, потом сразу перед ней
    after = t[m.end():].lstrip()
    before = t[:m.start()].rstrip()
    currency = None
    for sign, cur in (("₸", "₸"), ("тг", "₸"), ("тенге", "₸"), ("kzt", "₸"),
                      ("$", "$"), ("usd", "$"), ("долл", "$"), ("€", "€"), ("eur", "€"), ("евро", "€")):
        if after.startswith(sign):
            currency = cur
            break
    if currency is None:
        currency = next((cur for sign, cur in (("$", "$"), ("€", "€"), ("₸", "₸")) if before.endswith(sign)), None)
    currency = currency or _currency_in(t) or "₸"
    digits = re.sub(r"\D", "", m.group(0).split(",")[0])
    if not digits:
        return None, currency
    value = int(digits)
    # «4,5 млн» / «1.25 млн»
    if re.search(r"млн", t):
        num = float(m.group(0).replace(" ", "").replace(",", "."))
        value = int(num * 1_000_000)
    return value, currency


def money_text(value: int, currency: str) -> str:
    grouped = f"{value:,}".replace(",", " ")
    return f"${grouped}" if currency == "$" else f"{grouped} {currency}"


# ── результат ─────────────────────────────────────────────────────────────────
@dataclass
class Parsed:
    tour: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)   # что выбросили как «только для Instagram»


def parse(text: str, today: date, tour_id: str = "", photos: list[str] | None = None) -> Parsed:
    out = Parsed()
    raw_lines = [ln.rstrip() for ln in (text or "").replace("\r", "").split("\n")]

    # WhatsApp из поста: на этот номер и пойдут заявки из приложения
    phone = PHONE_RE.search(text or "")
    whatsapp = re.sub(r"\D", "", phone.group(0)) if phone else ""
    if whatsapp.startswith("8") and len(whatsapp) == 11:
        whatsapp = "7" + whatsapp[1:]

    # выбрасываем инстаграмные строки, хештеги, ники и строку с телефоном
    lines: list[str] = []
    for ln in raw_lines:
        s = ln.strip()
        only_tags = s and all(tok.startswith("#") or tok.startswith("@") for tok in s.split())
        if s and (NOISE.search(strip_decor(s)) or NOISE.search(s) or only_tags
                  or (phone and phone.group(0) in s)):
            out.dropped.append(s)
            continue
        lines.append(s)

    # абзацы
    blocks: list[list[str]] = []
    cur: list[str] = []
    for s in lines:
        if s:
            cur.append(s)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    if not blocks:
        out.errors.append("Пост пустой.")
        return out

    title = tidy_title(blocks[0][0])
    rest_of_first = blocks[0][1:]
    blocks = ([rest_of_first] if rest_of_first else []) + blocks[1:]

    summary_parts: list[str] = []
    description: list[str] = []
    route: list[dict] = []
    variants: list[dict] = []
    includes: list[str] = []
    excludes: list[str] = []
    highlights: list[str] = []
    programme: list[dict] = []
    extra = {"prepay": None, "group": None, "seats": None, "conditions": None}
    structured_started = False
    pending_list: list[str] | None = None    # заголовок списка без пунктов — пункты в следующем блоке

    def read_list(items: list[str], target: list[str]) -> None:
        for s in items:
            item = re.sub(r"^[\-–—•·*✓✔️☑️✅\s]+", "", s).strip()
            if not item:
                continue
            if re.match(r"^[\-–—•·*]", s) or not target:
                target.append(item[:1].upper() + item[1:])
            else:                      # продолжение предыдущего пункта
                target[-1] += " " + item

    bullet = re.compile(r"^[\-–—•·*▪◦✓✔☑✅🔹🔸]")
    for block in blocks:
        if pending_list is not None:
            target, pending_list = pending_list, None
            if all(bullet.match(x) for x in block):
                read_list(block, target)
                continue
        first_kind, first_val = label_of(block[0])
        head_flags = FLAG_RE.findall(block[0])
        has_dates = any(label_of(s)[0] == "dates" for s in block)
        has_price = any(label_of(s)[0] == "price" for s in block)

        # ── вариант тура: блок с «Даты:» и/или «Стоимость:» ──
        if has_dates or has_price:
            structured_started = True
            v: dict = {"flags": [], "route": None, "dates": None, "price": None, "currency": "₸"}
            for s in block:
                kind, val = label_of(s)
                if kind == "dates":
                    v["dates"] = val
                elif kind == "price":
                    v["price"], v["currency"] = parse_money(val)
                elif kind in extra and kind is not None:
                    extra[kind] = val
                elif FLAG_RE.search(s) and not kind:
                    v["flags"] = FLAG_RE.findall(s)
                    v["route"] = FLAG_RE.sub("", s).strip(" ,·-") or None
            variants.append(v)
            continue

        # ── списки ──
        if first_kind in ("includes", "excludes", "highlights"):
            structured_started = True
            target = {"includes": includes, "excludes": excludes, "highlights": highlights}[first_kind]
            # строки-метки внутри списка («Условия:» сразу под «Включено:») разбираем отдельно
            rest = []
            for s in block[1:]:
                kind, val = label_of(s)
                if kind in extra:
                    extra[kind] = val
                else:
                    rest.append(s)
            items = ([first_val] if first_val else []) + rest
            if first_val and "," in first_val and not rest:   # «Включено: перелёт, отели, гид»
                items = [f"- {x.strip()}" for x in first_val.split(",") if x.strip()]
            read_list(items, target)
            if not items:
                pending_list = target     # «☑ В стоимость входят:», пустая строка, затем пункты
            continue

        if first_kind == "programme":
            structured_started = True
            for s in ([first_val] if first_val else []) + block[1:]:
                m = re.match(r"^(день\s*\d+[^—–:-]*)[—–:-]\s*(.+)$", strip_decor(s), re.I)
                if m:
                    text_ = m.group(2).strip()
                    programme.append({"title": m.group(1).strip(" ·"), "text": text_[:1].upper() + text_[1:]})
                elif programme:
                    programme[-1]["text"] += " " + s
            continue

        # ── одиночные метки ──
        handled = False
        for s in block:
            kind, val = label_of(s)
            if kind in extra:
                extra[kind] = val
                handled = True
        if handled and all(label_of(s)[0] in extra for s in block):
            structured_started = True
            continue

        # ── остановка маршрута: «🇿🇦 Южно-Африканская Республика» + абзац ──
        if head_flags and len(block) >= 2 and len(FLAG_RE.sub("", block[0]).strip()) < 50:
            structured_started = True
            name = FLAG_RE.sub("", block[0]).strip()
            route.append({"title": f"{''.join(head_flags)} {name}".strip(),
                          "text": clean_tail(" ".join(block[1:])), "flags": head_flags})
            continue

        # ── свободный текст ──
        paragraph = clean_tail(" ".join(block))
        if not paragraph:
            continue
        lead = re.match(r"^((?:[\U0001F1E6-\U0001F1FF]{2}\s*)+)(.+)$", paragraph)
        if lead and len(lead.group(2).strip()) >= 30:
            fl = FLAG_RE.findall(lead.group(1))
            names = ", ".join(n for n in (COUNTRIES.get(flag_to_iso(f), "") for f in fl) if n)
            route.append({"title": f"{''.join(fl)} {names}".strip(),
                          "text": lead.group(2).strip(), "flags": fl})
            continue
        if not structured_started and not summary_parts:
            summary_parts.append(paragraph)
        else:
            description.append(paragraph)

    # ── страны: из маршрута, иначе из вариантов, иначе из названия ──
    flags: list[str] = []
    for src in [f for r in route for f in r["flags"]] + [f for v in variants for f in v["flags"]] + FLAG_RE.findall(title):
        if src not in flags:
            flags.append(src)
    countries = [COUNTRIES.get(flag_to_iso(f), "") for f in flags]
    countries = [c for c in countries if c]
    title = FLAG_RE.sub("", title).strip()

    # ── варианты → даты и цены ──
    clean_variants = []
    for v in variants:
        start, end = parse_dates(v["dates"] or "", today) if v["dates"] else (None, None)
        item = {
            "route": v["route"] or (", ".join(COUNTRIES.get(flag_to_iso(f), "") for f in v["flags"]) or None),
            "departure": start.isoformat() if start else None,
            "returnDate": end.isoformat() if end else None,
            "nights": (end - start).days if start and end else None,
            "datesNote": None if start else (v["dates"] or None),
            "priceFrom": v["price"],
            "currency": v["currency"],
        }
        clean_variants.append(item)

    priced = [v for v in clean_variants if v["priceFrom"]]
    dated = [v for v in clean_variants if v["departure"] or v["datesNote"]]
    if not title:
        out.errors.append("Нет названия в первой строке.")
    if not dated:
        out.errors.append("Не нашёл даты. Нужна строка вида «Даты: с 20 по 28 ноября».")
    if not priced:
        out.errors.append("Не нашёл цену. Нужна строка вида «Стоимость: $4 000» — цифрами.")
    if not countries:
        out.errors.append("Не понял страну: поставьте флажок в начале названия или перед названием страны.")
    if out.errors:
        return out

    base = clean_variants[0]
    cheapest = min(priced, key=lambda v: v["priceFrom"])
    prepay = None
    if extra["prepay"]:
        parts = [x.strip() for x in re.split(r"\s*[|/]\s*", extra["prepay"]) if x.strip()]
        shown = []
        for part in parts:
            amount, cur = parse_money(part)
            shown.append(money_text(amount, cur) if amount else part)
        prepay = " / ".join(shown) or None

    # Если вступления нет, а пост начинается сразу с маршрута — под названием
    # показываем первую фразу первой остановки.
    if not summary_parts and route:
        first = re.split(r"(?<=[.!?])\s+", route[0]["text"], maxsplit=1)[0]
        summary_parts.append(first)

    seats = None
    if extra["seats"]:
        m = re.search(r"\d+", extra["seats"])
        seats = int(m.group(0)) if m else None

    out.tour = {
        "id": tour_id,
        "title": title,
        "country": countries[0],
        "countries": countries,
        "flags": [f for f in flags if COUNTRIES.get(flag_to_iso(f))],
        "summary": summary_parts[0] if summary_parts else None,
        "description": "\n\n".join(description) or None,
        "departure": base["departure"],
        "returnDate": base["returnDate"],
        "nights": base["nights"],
        "datesNote": base["datesNote"],
        "priceFrom": cheapest["priceFrom"],
        "currency": cheapest["currency"],
        "variants": clean_variants if len(clean_variants) > 1 else None,
        "route": [{"title": r["title"], "text": r["text"]} for r in route] or None,
        "highlights": highlights or None,
        "programme": programme or None,
        "includes": includes or None,
        "excludes": excludes or None,
        "prepayment": prepay,
        "groupSize": extra["group"],
        "seatsLeft": seats,
        "note": extra["conditions"],
        "photos": photos or [],
        "whatsApp": whatsapp or None,
        "isSoldOut": bool(re.search(r"#набрано|группа\s+набрана", text or "", re.I)),
    }
    return out


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    result = parse(Path(sys.argv[1]).read_text("utf-8"), date.today(), "demo")
    print(json.dumps({"tour": result.tour, "errors": result.errors, "dropped": result.dropped},
                     ensure_ascii=False, indent=1))
