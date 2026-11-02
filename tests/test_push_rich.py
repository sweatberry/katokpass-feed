"""Уведомления с картинкой: фото тура и данные карточки рейса."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

from push import plan, deal_card, tour_image, Push, test_pushes, TEST_TOPIC  # noqa: E402

ALMATY = timezone(timedelta(hours=5))
EMPTY = {"day": "2026-10-30", "seenDeals": [], "seenTours": [], "topics": {}, "tourSnap": {}}


def at(hour: int) -> datetime:
    return datetime(2026, 10, 30, hour, 5, tzinfo=ALMATY)


DEAL = {"id": "a", "from": "Алматы", "to": "Анталия", "country": "Турция", "toCountry": "Турция",
        "departure": "2026-11-12", "returnDate": "2026-11-19", "price": 89000, "trip": "roundTrip",
        "airline": "SCAT"}
TOUR = {"id": "t1", "title": "Япония", "flags": ["🇯🇵"], "priceFrom": 4000, "currency": "$",
        "departure": "2027-04-01", "nights": 10, "photos": ["signature/123.jpg"]}


class Rich(unittest.TestCase):
    def test_deal_card(self):
        card = deal_card(DEAL, "ru")
        self.assertEqual(card["cardFlag"], "🇹🇷")
        self.assertEqual(card["cardFrom"], "Алматы")
        self.assertEqual(card["cardTo"], "Анталия")
        self.assertIn("89", card["cardPrice"])
        self.assertIn("12 ноября → 19 ноября", card["cardDates"])
        self.assertEqual(deal_card(DEAL, "kk")["cardTo"], "Анталья")

    def test_deal_push_has_card_and_mutable_content(self):
        pushes, _, _ = plan({"deals": [DEAL]}, None, EMPTY, at(12))
        msg = pushes[0].fcm()["message"]
        self.assertEqual(msg["apns"]["payload"]["aps"]["mutable-content"], 1)
        self.assertEqual(msg["data"]["cardFlag"], "🇹🇷")
        self.assertNotIn("image", msg["data"])

    def test_tour_push_has_photo(self):
        pushes, _, _ = plan(None, {"tours": [TOUR]}, EMPTY, at(12))
        msg = pushes[0].fcm()["message"]
        url = "https://sweatberry.github.io/katokpass-feed/signature/123.jpg"
        self.assertEqual(msg["data"]["image"], url)
        self.assertEqual(msg["apns"]["fcm_options"]["image"], url)

    def test_test_pushes_go_only_to_test_topic(self):
        pushes = test_pushes("all", {"deals": [DEAL]}, {"tours": [TOUR]})
        self.assertEqual(len(pushes), 3)
        self.assertTrue(all(p.topics == [TEST_TOPIC] for p in pushes))
        self.assertIn("Осталось 3 места", pushes[1].title)
        self.assertEqual(pushes[2].fcm()["message"]["data"]["cardFlag"], "🇹🇷")

    def test_tour_without_photo(self):
        self.assertIsNone(tour_image({**TOUR, "photos": []}))
        msg = Push(["x"], "t", "b", {}, "c", "s").fcm()["message"]
        self.assertNotIn("fcm_options", msg["apns"])


if __name__ == "__main__":
    unittest.main()
