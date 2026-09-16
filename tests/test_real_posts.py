"""Разбор настоящих постов канала (присланы 16.09.2026)."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "collector"))

from post_parser import parse_post  # noqa: E402

POSTED = datetime(2026, 9, 16, 17, 46, tzinfo=timezone.utc)  # 22:46 по Алматы


def parse(name):
    text = (HERE / "samples" / name).read_text("utf-8")
    return [d.to_json() for d in parse_post(text, 500, POSTED, "Step to Travel")]


class EgyptAstanaTests(unittest.TestCase):
    def setUp(self):
        self.deals = parse("egypt_astana.txt")

    def test_counts(self):
        one_way_out = [d for d in self.deals if d["from"] == "Астана" and d["trip"] == "oneWay"]
        one_way_back = [d for d in self.deals if d["to"] == "Астана" and d["trip"] == "oneWay"]
        round_trip = [d for d in self.deals if d["trip"] == "roundTrip"]
        self.assertEqual((len(one_way_out), len(one_way_back), len(round_trip)), (3, 8, 9))

    def test_first_row(self):
        d = self.deals[0]
        self.assertEqual((d["from"], d["to"], d["country"], d["toCountry"]),
                         ("Астана", "Шарм-эш-Шейх", "Египет", "Египет"))
        self.assertEqual((d["departure"], d["price"], d["seats"]), ("2026-09-17", 67000, 2))
        self.assertEqual(d["airline"], "Чартер")
        self.assertEqual(d["baggage"], "23 + 8 кг")

    def test_airline_codes(self):
        by_key = {(d["from"], d["departure"], d["price"]): d for d in self.deals}
        scat = by_key[("Шарм-эш-Шейх", "2026-09-17", 27000)]
        self.assertEqual((scat["airline"], scat["baggage"], scat["seats"]), ("SCAT", "23 + 5 кг", 5))
        cairo = by_key[("Астана", "2026-09-18", 88000)]  # «А» кириллицей
        self.assertEqual((cairo["airline"], cairo["baggage"]), ("Air Cairo", "20 + 5 кг"))
        back = by_key[("Шарм-эш-Шейх", "2026-09-17", 36000)]
        self.assertEqual((back["country"], back["toCountry"]), ("Египет", "Казахстан"))

    def test_round_trip(self):
        rt = [d for d in self.deals if d["trip"] == "roundTrip"]
        self.assertEqual((rt[0]["departure"], rt[0]["returnDate"], rt[0]["price"]),
                         ("2026-09-18", "2026-09-25", 257000))
        mixed = [d for d in rt if d["departure"] == "2026-09-24"][0]
        self.assertEqual((mixed["airline"], mixed["returnDate"]), ("Air Cairo / SCAT", "2026-10-01"))
        self.assertEqual(mixed["baggage"],
                         "зависит от авиакомпании: Air Cairo — 20 + 5 кг, SCAT — 23 + 5 кг")
        self.assertTrue(all(d["from"] == "Астана" and d["to"] == "Шарм-эш-Шейх" for d in rt))


class EgyptRegionsTests(unittest.TestCase):
    def test_all_cities(self):
        deals = parse("egypt_regions.txt")
        routes = {(d["from"], d["to"]) for d in deals}
        self.assertEqual(routes, {
            ("Атырау", "Шарм-эш-Шейх"), ("Актобе", "Шарм-эш-Шейх"), ("Шарм-эш-Шейх", "Актобе"),
            ("Шымкент", "Шарм-эш-Шейх"), ("Шарм-эш-Шейх", "Шымкент"), ("Костанай", "Шарм-эш-Шейх"),
            ("Шарм-эш-Шейх", "Костанай"), ("Уральск", "Шарм-эш-Шейх"), ("Петропавловск", "Шарм-эш-Шейх"),
        })
        self.assertEqual(len(deals), 17)
        pp = [d for d in deals if d["from"] == "Петропавловск"][0]
        self.assertEqual((pp["departure"], pp["price"], pp["airline"]), ("2026-11-08", 32000, "SCAT"))
        self.assertTrue(all(d["trip"] == "oneWay" for d in deals))


class ItalyTests(unittest.TestCase):
    def test_italy(self):
        deals = parse("italy.txt")
        out = [d for d in deals if d["from"] == "Алматы"]
        back = [d for d in deals if d["to"] == "Алматы"]
        self.assertEqual((len(out), len(back)), (19, 18))  # «39.01» — опечатка, пропускаем
        self.assertTrue(all(d["airline"] == "Neos" and d["baggage"] == "23 + 8 кг" for d in deals))
        self.assertEqual(out[0]["country"], "Италия")
        jan = [d for d in out if d["departure"].endswith("-01-02")][0]
        self.assertEqual(jan["departure"], "2027-01-02")
        self.assertEqual(back[0]["toCountry"], "Казахстан")


if __name__ == "__main__":
    unittest.main()
