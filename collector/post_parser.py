"""Разбор постов канала в список рейсов.

Пост может описывать один рейс или несколько. Разборщик:
1. находит все упоминания городов из places.py;
2. пары «город — разделитель — город» считает маршрутами;
3. для каждого маршрута берёт кусок текста до следующего маршрута
   и ищет в нём даты, цену, авиакомпанию и багаж.
Рейс без маршрута, даты или цены в тенге пропускается.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta

from places import AIRLINES, ALIASES, HOME_COUNTRY, IATA

# ── города ────────────────────────────────────────────────────────────────

_alias_keys = sorted(ALIASES, key=len, reverse=True)
_alias_re = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(a) for a in _alias_keys) + r")([а-яё]{0,2})(?![\w])",
    re.IGNORECASE,
)
_iata_re = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(IATA)) + r")(?![A-Za-z])")

# Что может стоять между двумя городами маршрута.
_SEP_RE = re.compile(
    r"^(?:[\s\-–—−‐→⟶➡⇄⇆↔↗>~/|✈🛫🛬️]|->|=>|в|во|to|до)*$",
    re.IGNORECASE,
)
_RETURN_SEP_RE = re.compile(r"[⇄⇆↔]")


@dataclass
class Mention:
    start: int
    end: int
    city: str
    country: str


def find_cities(text: str) -> list[Mention]:
    found: list[Mention] = []
    for m in _alias_re.finditer(text):
        base = m.group(1).lower()
        # Окончание разрешаем только у длинных названий: «Анталию», «Хургаду».
        if m.group(2) and len(base) < 5:
            continue
        city, country = ALIASES[base]
        found.append(Mention(m.start(), m.end(), city, country))
    for m in _iata_re.finditer(text):
        city, country = IATA[m.group(1)]
        found.append(Mention(m.start(), m.end(), city, country))
    found.sort(key=lambda x: x.start)
    # убираем пересечения (длинное совпадение важнее)
    result: list[Mention] = []
    for m in found:
        if result and m.start < result[-1].end:
            continue
        result.append(m)
    return result


@dataclass
class Route:
    start: int
    end: int
    origin: Mention
    dest: Mention
    round_trip: bool


def find_routes(text: str) -> list[Route]:
    cities = find_cities(text)
    routes: list[Route] = []
    i = 0
    while i < len(cities) - 1:
        a, b = cities[i], cities[i + 1]
        between = text[a.end:b.start]
        if a.city != b.city and "\n" not in between and _SEP_RE.match(between.strip()):
            round_trip = bool(_RETURN_SEP_RE.search(between))
            end = b.end
            skip = 2
            # «ALA – SSH – ALA»: туда-обратно
            if i + 2 < len(cities):
                c = cities[i + 2]
                between2 = text[b.end:c.start]
                if c.city == a.city and "\n" not in between2 and _SEP_RE.match(between2.strip()):
                    round_trip = True
                    end = c.end
                    skip = 3
            routes.append(Route(a.start, end, a, b, round_trip))
            i += skip
        else:
            i += 1
    return routes


# ── даты ──────────────────────────────────────────────────────────────────

_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
_range_re = re.compile(r"(?<![\d./])(\d{1,2})\s*[-–—]\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?!\d)")
_num_date_re = re.compile(r"(?<![\d.,])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d])")
_word_date_re = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:[-–—]\s*(\d{1,2})\s*)?"
    r"(янв|фев|мар|апр|мая|май|июн|июл|авг|сен|окт|ноя|дек)[а-я]*\.?",
    re.IGNORECASE,
)


def _make_date(day: int, month: int, year: int | None, posted: date) -> date | None:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if year is not None and year < 100:
        year += 2000
    y = year or posted.year
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    if year is None and d < posted - timedelta(days=7):
        try:
            d = date(y + 1, month, day)
        except ValueError:
            return None
    return d


def find_dates(text: str, posted: date) -> list[tuple[int, date]]:
    """Даты в порядке появления: [(позиция, дата)]. Диапазон «17-24.09» даёт две даты."""
    found: list[tuple[int, date]] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= ts or s >= te for ts, te in taken)

    for m in _range_re.finditer(text):
        month = int(m.group(3))
        year = int(m.group(4)) if m.group(4) else None
        d1 = _make_date(int(m.group(1)), month, year, posted)
        d2 = _make_date(int(m.group(2)), month, year, posted)
        if d1 and d2 and d2 >= d1:
            found += [(m.start(), d1), (m.start() + 1, d2)]
            taken.append((m.start(), m.end()))
    for m in _word_date_re.finditer(text):
        if not free(m.start(), m.end()):
            continue
        month = _MONTHS[m.group(3).lower()[:3]]
        d1 = _make_date(int(m.group(1)), month, None, posted)
        if not d1:
            continue
        found.append((m.start(), d1))
        if m.group(2):
            d2 = _make_date(int(m.group(2)), month, None, posted)
            if d2 and d2 >= d1:
                found.append((m.start() + 1, d2))
        taken.append((m.start(), m.end()))
    for m in _num_date_re.finditer(text):
        if not free(m.start(), m.end()):
            continue
        year = int(m.group(3)) if m.group(3) else None
        d = _make_date(int(m.group(1)), int(m.group(2)), year, posted)
        if d:
            found.append((m.start(), d))
            taken.append((m.start(), m.end()))
    found.sort()
    return found


# ── цена, багаж, авиакомпания ─────────────────────────────────────────────

_price_re = re.compile(
    r"(?<![\d])(\d{1,3}(?:[   .,]\d{3})+|\d{4,7})\s*"
    r"(?:₸|тг\b\.?|тенге|kzt\b|т\.(?!\w))",
    re.IGNORECASE,
)
_price_thousands_re = re.compile(r"(?<![\d.,])(\d{1,4})\s*(?:тыс\.?|к)(?![а-яё\w])", re.IGNORECASE)
_price_label_re = re.compile(
    r"(?:цена|стоимость|price)\s*[:\-–—]?\s*(?:от\s*)?(\d{1,3}(?:[   .,]\d{3})+|\d{4,7})",
    re.IGNORECASE,
)


def find_prices(text: str) -> list[int]:
    prices: list[int] = []
    for rx in (_price_re, _price_label_re, _price_thousands_re):
        for m in rx.finditer(text):
            value = int(re.sub(r"\D", "", m.group(1)))
            if rx is _price_thousands_re:
                value *= 1000
            if 5_000 <= value <= 5_000_000:
                prices.append(value)
    return prices


_bag_pair_re = re.compile(r"(?<!\d)(\d{1,2})\s*\+\s*(\d{1,2})\s*(?:кг|kg)?", re.IGNORECASE)
_bag_single_re = re.compile(r"багаж\D{0,12}?(\d{1,2})\s*(?:кг|kg)", re.IGNORECASE)
_no_bag_re = re.compile(r"без\s+багаж", re.IGNORECASE)


def find_baggage(text: str) -> str | None:
    m = _bag_pair_re.search(text)
    if m:
        return f"{int(m.group(1))} + {int(m.group(2))} кг"
    m = _bag_single_re.search(text)
    if m:
        return f"{int(m.group(1))} кг"
    if _no_bag_re.search(text):
        return "только ручная кладь"
    return None


def find_airline(text: str) -> str | None:
    low = text.lower()
    for name, variants in AIRLINES:
        for v in variants:
            if re.search(r"(?<![\w])" + re.escape(v) + r"(?![\w])", low):
                return name
    return None


_round_words_re = re.compile(r"туда[\s-]*(?:и\s*)?обратно|т/о|\bRT\b|\bобратно\b|возврат", re.IGNORECASE)


# ── сборка ────────────────────────────────────────────────────────────────

@dataclass
class Deal:
    id: str
    origin: str
    destination: str
    country: str            # страна направления (не Казахстан, если это возможно)
    fromCountry: str
    toCountry: str          # страна прилёта — по ней фильтрует приложение
    departure: str          # YYYY-MM-DD
    returnDate: str | None
    price: int
    trip: str               # oneWay | roundTrip
    airline: str
    baggage: str
    agency: str
    postedAt: str           # ISO 8601, UTC
    expiresAt: str
    sourcePostID: int
    seats: int | None = None

    def to_json(self) -> dict:
        d = asdict(self)
        d["from"] = d.pop("origin")
        d["to"] = d.pop("destination")
        return d


def _country_for(route: Route) -> str:
    if route.dest.country != HOME_COUNTRY:
        return route.dest.country
    if route.origin.country != HOME_COUNTRY:
        return route.origin.country
    return HOME_COUNTRY


# ── посты-таблицы: «S 17.09 - 27 000 (5) 🔥» под заголовком маршрута ─────────

_row_re = re.compile(
    r"^\s*(?P<code>[A-Za-zА-Яа-яЁё*]{1,3}(?:\s*/\s*[A-Za-zА-Яа-яЁё*]{1,3})*)?\s*"
    r"(?P<d1>\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)"
    r"(?:\s*[-–—]\s*(?P<d2>\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?))?"
    r"\s*[-–—=:]\s*(?:от\s*)?"
    r"(?P<price>\d{1,3}(?:[   .,]\d{3})+|\d{4,7})"
    r"(?P<rest>.*)$"
)
_seats_re = re.compile(r"\((\d{1,3})\)")
_legend_re = re.compile(r"^\s*(?P<name>[A-Za-zА-Яа-яЁё][\w .&'-]{1,30}?)\s*[-–—:]\s*багаж", re.IGNORECASE)
_kg_re = re.compile(r"(\d{1,2})\s*(?:кг|kg)", re.IGNORECASE)
_round_header_re = re.compile(r"туда[\s-]*(?:и\s*)?обратно", re.IGNORECASE)
_foreign_currency_re = re.compile(r"\$|\busd\b|\beur\b|€|евро|\bруб|₽", re.IGNORECASE)
_LATIN_LOOKALIKE = str.maketrans("АВЕКМНОРСТХаеорсх", "ABEKMHOPCTXaeopcx")


def _bag_text(line: str) -> str | None:
    nums = [int(n) for n in _kg_re.findall(line)]
    if len(nums) >= 2:
        return f"{nums[0]} + {nums[1]} кг"
    if nums:
        return f"{nums[0]} кг"
    return None


def _parse_date(raw: str, posted: date) -> date | None:
    parts = re.split(r"[./]", raw)
    year = int(parts[2]) if len(parts) > 2 else None
    return _make_date(int(parts[0]), int(parts[1]), year, posted)


def parse_table_post(text: str, post_id: int, posted_at: datetime, agency: str,
                     ttl: timedelta) -> list["Deal"]:
    lines = text.splitlines()
    rows = [ln for ln in lines if _row_re.match(ln)]
    if len(rows) < 1 or _foreign_currency_re.search(text):
        return []

    # Расшифровка в конце поста: «Scat - багаж 23 кг + ручная кладь 5 кг».
    legend: dict[str, tuple[str, str | None]] = {}
    default_bag = None
    for ln in lines:
        m = _legend_re.match(ln)
        if m:
            name = find_airline(m.group("name")) or m.group("name").strip()
            if m.group("name").strip().lower() in ("багаж",):
                default_bag = _bag_text(ln)
                continue
            legend[name[0].upper()] = (name, _bag_text(ln))
        elif re.match(r"^\s*багаж", ln, re.IGNORECASE) and default_bag is None:
            default_bag = _bag_text(ln)
    single_airline = next(iter(legend.values())) if len(legend) == 1 else None
    if not legend:
        post_airline = find_airline(text)
        if post_airline:
            single_airline = (post_airline, find_baggage(text))

    def airline_for(code: str | None) -> tuple[str, str]:
        if code:
            names, bags = [], []
            for part in re.split(r"\s*/\s*", code.translate(_LATIN_LOOKALIKE).upper()):
                if part in legend:
                    names.append(legend[part][0])
                    bags.append(legend[part][1])
            if names:
                if len(set(bags)) == 1 and bags[0]:
                    bag = bags[0]
                elif len(names) > 1:
                    bag = "зависит от авиакомпании: " + ", ".join(
                        f"{n} — {b}" for n, b in zip(names, bags) if b)
                else:
                    bag = default_bag or "уточняйте"
                return " / ".join(dict.fromkeys(names)), bag
        if single_airline and not code:
            return single_airline[0], single_airline[1] or default_bag or "уточняйте"
        return "Чартер", default_bag or "уточняйте"

    posted_day = posted_at.date()
    expires = posted_at + ttl
    origin = dest = None
    round_header = False
    deals: list[Deal] = []
    seen: set[tuple] = set()

    for ln in lines:
        row = _row_re.match(ln)
        if not row:
            cities = find_cities(ln)
            if len(cities) >= 2 and cities[0].city != cities[1].city:
                origin, dest = cities[0], cities[1]
                round_header = len(cities) >= 3 and cities[2].city == cities[0].city
            elif _round_header_re.search(ln) and origin is not None:
                round_header = True
            continue
        if origin is None or dest is None:
            continue
        d1 = _parse_date(row.group("d1"), posted_day)
        d2 = _parse_date(row.group("d2"), posted_day) if row.group("d2") else None
        if d1 is None or d1 < posted_day:
            continue
        if d2 is not None and d2 < d1:
            d2 = None
        price = int(re.sub(r"\D", "", row.group("price")))
        if not 5_000 <= price <= 5_000_000:
            continue
        seats_m = _seats_re.search(row.group("rest") or "")
        airline, baggage = airline_for((row.group("code") or "").strip() or None)
        key = (origin.city, dest.city, d1, d2, price, airline)
        if key in seen:
            continue
        seen.add(key)
        is_round = d2 is not None or round_header
        deals.append(Deal(
            id=f"{post_id}-{len(deals)}",
            origin=origin.city,
            destination=dest.city,
            country=_country_for(Route(0, 0, origin, dest, is_round)),
            fromCountry=origin.country,
            toCountry=dest.country,
            departure=d1.isoformat(),
            returnDate=d2.isoformat() if d2 else None,
            price=price,
            trip="roundTrip" if is_round else "oneWay",
            airline=airline,
            baggage=baggage,
            agency=agency,
            postedAt=posted_at.isoformat().replace("+00:00", "Z"),
            expiresAt=expires.isoformat().replace("+00:00", "Z"),
            sourcePostID=post_id,
            seats=int(seats_m.group(1)) if seats_m else None,
        ))
    return deals


def parse_post(text: str, post_id: int, posted_at: datetime, agency: str,
               ttl: timedelta = timedelta(hours=24)) -> list[Deal]:
    if not text:
        return []
    table = parse_table_post(text, post_id, posted_at, agency, ttl)
    if table:
        return table
    posted_day = posted_at.date()
    expires = posted_at + ttl
    routes = find_routes(text)
    post_airline = find_airline(text)
    post_baggage = find_baggage(text)

    deals: list[Deal] = []
    seen: set[tuple] = set()

    for n, route in enumerate(routes):
        seg_end = routes[n + 1].start if n + 1 < len(routes) else len(text)
        segment = text[route.end:seg_end]
        airline = find_airline(segment) or post_airline or "Чартер"
        baggage = find_baggage(segment) or post_baggage or "уточняйте"
        round_trip = route.round_trip or bool(_round_words_re.search(segment))

        # Вариант 1: несколько строк «дата — цена» под одним маршрутом.
        offers: list[tuple[list[date], int]] = []
        for line in segment.splitlines():
            ds = [d for _, d in find_dates(line, posted_day)]
            ps = find_prices(line)
            if ds and ps:
                offers.append((ds, min(ps)))
        # Вариант 2: даты и цена разбросаны по сегменту.
        if not offers:
            ds = [d for _, d in find_dates(segment, posted_day)]
            ps = find_prices(segment)
            if ds and ps:
                offers.append((ds, min(ps)))

        for ds, price in offers:
            departure = ds[0]
            back = None
            if round_trip and len(ds) > 1 and ds[1] > departure:
                back = ds[1]
            elif not route.round_trip and len(ds) > 1 and ds[1] > departure and _round_words_re.search(segment):
                back = ds[1]
            if departure < posted_day:
                continue
            key = (route.origin.city, route.dest.city, departure, back, price)
            if key in seen:
                continue
            seen.add(key)
            deals.append(Deal(
                id=f"{post_id}-{len(deals)}",
                origin=route.origin.city,
                destination=route.dest.city,
                country=_country_for(route),
                fromCountry=route.origin.country,
                toCountry=route.dest.country,
                departure=departure.isoformat(),
                returnDate=back.isoformat() if back else None,
                price=price,
                trip="roundTrip" if back or round_trip else "oneWay",
                airline=airline,
                baggage=baggage,
                agency=agency,
                postedAt=posted_at.isoformat().replace("+00:00", "Z"),
                expiresAt=expires.isoformat().replace("+00:00", "Z"),
                sourcePostID=post_id,
            ))
    return deals
