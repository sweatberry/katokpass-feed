"""Тесты разборщика. Тексты ниже — придуманные примеры в разных форматах,
а не реальные посты канала. Когда будут реальные посты, их стоит добавить сюда."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

from post_parser import parse_post, find_cities  # noqa: E402

POSTED = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)


def parse(text):
    return [d.to_json() for d in parse_post(text, 101, POSTED, "Step to Travel")]


class ParserTests(unittest.TestCase):
    def test_single_block(self):
        deals = parse("""🔥 ГОРЯЩИЙ ЧАРТЕР
✈️ Шарм-эш-Шейх – Алматы
📅 17.09
💰 26 000 тг
SCAT, багаж 23+5 кг""")
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual((d["from"], d["to"], d["country"]), ("Шарм-эш-Шейх", "Алматы", "Египет"))
        self.assertEqual(d["departure"], "2026-09-17")
        self.assertEqual(d["price"], 26000)
        self.assertEqual(d["airline"], "SCAT")
        self.assertEqual(d["baggage"], "23 + 5 кг")
        self.assertEqual(d["trip"], "oneWay")
        self.assertEqual(d["expiresAt"], "2026-09-17T08:00:00Z")
        self.assertEqual((d["fromCountry"], d["toCountry"]), ("Египет", "Казахстан"))

    def test_several_routes_in_one_post(self):
        deals = parse("""Горящие места!
Алматы → Нячанг 18 сентября — 70.000₸
Санья → Алматы 19 сентября — 74 000 ₸
Алматы → Анталия 19/09 — 83000 тенге, Turkish, 20+8""")
        self.assertEqual([(d["from"], d["to"], d["price"]) for d in deals], [
            ("Алматы", "Нячанг", 70000),
            ("Санья", "Алматы", 74000),
            ("Алматы", "Анталия", 83000),
        ])
        self.assertEqual(deals[2]["airline"], "Turkish")
        self.assertEqual(deals[2]["baggage"], "20 + 8 кг")
        self.assertEqual(deals[0]["country"], "Вьетнам")

    def test_dates_with_prices_under_route(self):
        deals = parse("""ALA - AYT
19.09 - 83 000 тг
21.09 - 90 000 тг
Southwind""")
        self.assertEqual([(d["departure"], d["price"]) for d in deals],
                         [("2026-09-19", 83000), ("2026-09-21", 90000)])
        self.assertTrue(all(d["airline"] == "Southwind" for d in deals))

    def test_round_trip_range(self):
        deals = parse("Алматы – Белград – Алматы\n23-28.09\n257 000 тг туда-обратно, SCAT")
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual(d["trip"], "roundTrip")
        self.assertEqual((d["departure"], d["returnDate"]), ("2026-09-23", "2026-09-28"))

    def test_next_year_and_declension(self):
        deals = parse("Из Астаны в Хургаду 03.01, 150 тыс тг")
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["from"], "Астана")
        self.assertEqual(deals[0]["to"], "Хургада")
        self.assertEqual(deals[0]["departure"], "2027-01-03")
        self.assertEqual(deals[0]["price"], 150000)

    def test_skips_posts_without_price_or_route(self):
        self.assertEqual(parse("Друзья, всем хороших выходных! Скоро новые рейсы в Дубай"), [])
        self.assertEqual(parse("Алматы – Дубай 20.09, цену уточняйте в личке"), [])

    def test_past_departure_is_skipped(self):
        self.assertEqual(parse("Алматы - Дубай 10.09 - 60 000 тг"), [])

    def test_price_label_without_currency(self):
        deals = parse("Шымкент — Шарджа\nВылет: 25.09\nЦена: 55000")
        self.assertEqual(len(deals), 1)
        self.assertEqual((deals[0]["from"], deals[0]["country"], deals[0]["price"]), ("Шымкент", "ОАЭ", 55000))

    def test_iata_codes_only_uppercase(self):
        names = [m.city for m in find_cities("we can go ALA-SSH")]
        self.assertEqual(names, ["Алматы", "Шарм-эш-Шейх"])

    def test_no_bag(self):
        deals = parse("Алматы - Стамбул 30.09 45 000 тг, без багажа, AJet")
        self.assertEqual(deals[0]["baggage"], "только ручная кладь")
        self.assertEqual(deals[0]["airline"], "AJet")


if __name__ == "__main__":
    unittest.main()
