"""Разбор постов об авторских турах: настоящий пост из Instagram и шаблон из памятки."""
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "collector"))

from signature_parser import parse  # noqa: E402

TODAY = date(2026, 10, 28)


def sample(name: str) -> str:
    return (HERE / "samples" / name).read_text("utf-8")


class AfricaPost(unittest.TestCase):
    """Пост «Великое путешествие по Африке» — как он опубликован в Instagram, без правок."""

    @classmethod
    def setUpClass(cls):
        cls.result = parse(sample("signature_africa.txt"), TODAY, "42")
        cls.tour = cls.result.tour

    def test_no_errors(self):
        self.assertEqual(self.result.errors, [])

    def test_title(self):
        self.assertEqual(self.tour["title"], "Великое путешествие по Африке! 🦁")

    def test_countries_from_flags(self):
        self.assertEqual(self.tour["countries"], ["ЮАР", "Зимбабве", "Замбия", "Намибия", "Ботсвана"])

    def test_summary_without_pointer_emoji(self):
        self.assertTrue(self.tour["summary"].startswith("Уже этой осенью"))
        self.assertNotIn("👇", self.tour["summary"])

    def test_route_stops(self):
        titles = [r["title"] for r in self.tour["route"]]
        self.assertEqual(titles[0], "🇿🇦 Южно-Африканская Республика")
        self.assertEqual(len(titles), 5)
        self.assertIn("Виктория", self.tour["route"][1]["text"])

    def test_two_variants(self):
        v = self.tour["variants"]
        self.assertEqual(len(v), 2)
        self.assertEqual((v[0]["departure"], v[0]["returnDate"], v[0]["nights"]), ("2026-11-20", "2026-11-28", 8))
        self.assertEqual((v[1]["departure"], v[1]["returnDate"], v[1]["nights"]), ("2026-11-20", "2026-12-02", 12))
        self.assertEqual([x["priceFrom"] for x in v], [4000, 4500])
        self.assertTrue(v[1]["route"].endswith("Намибия"))

    def test_price_from_is_cheapest_in_dollars(self):
        self.assertEqual((self.tour["priceFrom"], self.tour["currency"]), (4000, "$"))

    def test_prepayment(self):
        self.assertEqual(self.tour["prepayment"], "$1 000")

    def test_includes_and_excludes(self):
        self.assertEqual(len(self.tour["includes"]), 11)
        self.assertEqual(self.tour["includes"][0], "Внутренние перелеты")
        self.assertEqual(len(self.tour["excludes"]), 6)
        self.assertEqual(self.tour["excludes"][0], "Международные перелеты")

    def test_whatsapp_number_from_post(self):
        self.assertEqual(self.tour["whatsApp"], "77787379977")

    def test_instagram_only_lines_dropped(self):
        dropped = " ".join(self.result.dropped)
        self.assertIn("в комментах", dropped)
        self.assertIn("шапке профиля", dropped)
        self.assertIn("Telegram", dropped)
        self.assertIn("#southafrica", dropped)
        self.assertIsNone(self.tour["description"])


class TemplatePost(unittest.TestCase):
    """Шаблон из памятки — тоже должен разбираться."""

    def test_iceland(self):
        r = parse(sample("signature_iceland.txt"), TODAY, "7")
        t = r.tour
        self.assertEqual(r.errors, [])
        self.assertEqual(t["title"], "Исландия: охота за северным сиянием")
        self.assertEqual(t["country"], "Исландия")
        self.assertEqual((t["departure"], t["nights"]), ("2027-02-07", 9))
        self.assertEqual((t["priceFrom"], t["currency"]), (1250000, "₸"))
        self.assertEqual(t["seatsLeft"], 4)
        self.assertEqual(t["includes"], ["Перелёт", "Отели", "Завтраки", "Транспорт", "Гид", "Страховка"])
        self.assertEqual(t["note"], "депозит 30%, остаток за месяц до вылета")
        self.assertEqual(len(t["programme"]), 2)
        self.assertIsNone(t["variants"])


class BrokenPosts(unittest.TestCase):
    """Если чего-то не хватает — тур не публикуем, а объясняем, чего именно."""

    def test_no_price(self):
        r = parse("🇯🇵 Япония\n\nДаты: с 3 по 14 апреля", TODAY)
        self.assertTrue(any("цену" in e for e in r.errors))
        self.assertEqual(r.tour, {})

    def test_no_dates(self):
        r = parse("🇯🇵 Япония\n\nСтоимость: $3 000", TODAY)
        self.assertTrue(any("даты" in e for e in r.errors))

    def test_no_country(self):
        r = parse("Большое путешествие\n\nДаты: с 3 по 14 апреля\nСтоимость: $3 000", TODAY)
        self.assertTrue(any("страну" in e for e in r.errors))

    def test_sold_out_tag(self):
        r = parse("🇯🇵 Япония\n\nДаты: с 3 по 14 апреля\nСтоимость: $3 000\n\n#набрано", TODAY)
        self.assertTrue(r.tour["isSoldOut"])

    def test_year_rolls_over(self):
        r = parse("🇯🇵 Япония\n\nДаты: с 28 декабря по 8 января\nСтоимость: $3 000", TODAY)
        self.assertEqual((r.tour["departure"], r.tour["returnDate"]), ("2026-12-28", "2027-01-08"))


if __name__ == "__main__":
    unittest.main()
