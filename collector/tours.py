"""Горящие туры: модель, проверка и файл tours.json.

Тур — это пакет: перелёт, отель, питание, страховка и трансфер.
Цена указывается за двоих целиком, как принято у туроператоров.
Заявка по туру уходит напрямую в агентство, поэтому у каждого тура
свой номер WhatsApp.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "tours.json"
PHOTOS = ROOT / "docs" / "photos"

DEFAULT_TTL_DAYS = 5.0

# Питание: как пишут агентства → как показываем в приложении.
MEALS = {
    "ro": ("RO", "без питания"),
    "bb": ("BB", "завтраки"),
    "hb": ("HB", "завтрак и ужин"),
    "fb": ("FB", "трёхразовое"),
    "ai": ("AI", "всё включено"),
    "uai": ("UAI", "ультра всё включено"),
}

DEFAULT_INCLUDES = ["Перелёт", "Проживание", "Питание", "Страховка", "Трансфер"]


def meals_code(raw: str) -> tuple[str, str] | None:
    key = re.sub(r"[^a-zа-я]", "", (raw or "").strip().lower())
    key = key.replace("всёвключено", "ai").replace("всевключено", "ai")
    key = key.replace("ультра", "uai")
    return MEALS.get(key)


def parse_date(raw: str, today: date) -> date | None:
    """«22.10» или «22.10.2026» → дата. Без года берём ближайшую будущую."""
    parts = re.split(r"[./-]", (raw or "").strip())
    if len(parts) < 2 or not all(p.strip().isdigit() for p in parts[:2]):
        return None
    day, month = int(parts[0]), int(parts[1])
    year = int(parts[2]) if len(parts) > 2 else today.year
    if year < 100:
        year += 2000
    try:
        result = date(year, month, day)
    except ValueError:
        return None
    if len(parts) == 2 and result < today:
        try:
            result = date(year + 1, month, day)
        except ValueError:
            return None
    return result


def parse_price(raw: str) -> int | None:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    value = int(digits)
    return value if 30_000 <= value <= 20_000_000 else None


def parse_int(raw: str, low: int, high: int) -> int | None:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    value = int(digits)
    return value if low <= value <= high else None


@dataclass
class Tour:
    id: str
    country: str
    resort: str
    hotel: str
    stars: int
    from_: str                 # город вылета
    departure: str             # YYYY-MM-DD
    nights: int
    meals: str                 # AI, UAI, HB…
    mealsLabel: str            # «всё включено»
    price: int                 # за двоих целиком, тенге
    includes: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDES))
    photo: str | None = None   # путь внутри docs/, например photos/abc.jpg
    note: str = ""
    agency: str = ""
    whatsApp: str = ""
    postedAt: str = ""
    expiresAt: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

    @classmethod
    def from_json(cls, data: dict) -> "Tour":
        d = dict(data)
        d["from_"] = d.pop("from", d.pop("from_", ""))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def title(self) -> str:
        stars = "★" * self.stars if self.stars else ""
        return f"{self.hotel} {stars}".strip()

    def summary(self) -> str:
        when = ".".join(reversed(self.departure.split("-")[1:])) if self.departure else "—"
        price = f"{self.price:,}".replace(",", " ")
        return (f"{self.country}, {self.resort}\n"
                f"{self.title}\n"
                f"{self.from_} · вылет {when} · {self.nights} ночей · {self.mealsLabel}\n"
                f"{price} ₸ за двоих")


def load(path: Path | None = None) -> list[dict]:
    path = path or OUTPUT
    if not path.exists():
        return []
    try:
        return (json.loads(path.read_text("utf-8")) or {}).get("tours", []) or []
    except json.JSONDecodeError:
        return []


def save(tours: list[dict], settings: dict, path: Path | None = None) -> bool:
    """Записывает файл, если он изменился. Возвращает True, если была запись."""
    path = path or OUTPUT
    now = datetime.now(timezone.utc)
    alive = [t for t in tours
             if datetime.fromisoformat(t["expiresAt"].replace("Z", "+00:00")) > now]
    alive.sort(key=lambda t: (t["departure"], t["price"]))
    old_tours = load(path)
    if old_tours == alive and path.exists():
        return False
    payload = {
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
        "settings": settings,
        "tours": alive,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return True


def add(tour: Tour, ttl_days: float = DEFAULT_TTL_DAYS, path: Path | None = None) -> list[dict]:
    """Добавляет тур к существующим, убирая дубли того же отеля и даты."""
    path = path or OUTPUT
    now = datetime.now(timezone.utc)
    tour.postedAt = now.isoformat().replace("+00:00", "Z")
    tour.expiresAt = (now + timedelta(days=ttl_days)).isoformat().replace("+00:00", "Z")
    item = tour.to_json()
    key = (item["hotel"].lower(), item["departure"], item["from"], item["agency"])
    tours = [t for t in load(path)
             if (t["hotel"].lower(), t["departure"], t["from"], t.get("agency", "")) != key]
    tours.append(item)
    return tours
