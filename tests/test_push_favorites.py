"""Новости по избранным турам: «осталось N мест» и «цена снизилась»."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

from push import plan, tour_topic, favorite_message  # noqa: E402

ALMATY = timezone(timedelta(hours=5))


def at(hour: int, day: int = 30) -> datetime:
    return datetime(2026, 10, day, hour, 5, tzinfo=ALMATY)


def tour(seats=None, price=4000, sold=False) -> dict:
    t = {"id": "sweatberry_app-120", "title": "Япония", "flags": ["🇯🇵"], "priceFrom": price,
         "currency": "$", "departure": "2027-04-01", "nights": 10}
    if seats is not None:
        t["seatsLeft"] = seats
    if sold:
        t["isSoldOut"] = True
    return t


def docs(t):
    return {"tours": [t]}


def state_with(t, day="2026-10-30"):
    """Состояние после прошлого запуска: тур уже известен и сфотографирован."""
    _, state, _ = plan(None, docs(t), {"day": day, "seenDeals": [], "seenTours": [t["id"]], "topics": {}}, at(12))
    return state


def fav_pushes(pushes):
    return [p for p in pushes if p.topics[0].startswith("st_")]


class Topic(unittest.TestCase):
    def test_known_value(self):
        # То же значение проверяет приложение (PushTopics.favoriteTour).
        self.assertEqual(tour_topic("sweatberry_app-120", "ru"), "st_42c2109f919d739482e93591")
        self.assertRegex(tour_topic("sweatberry_app-120", "kk"), r"^st_[0-9a-f]{24}$")
        self.assertNotEqual(tour_topic("a", "ru"), tour_topic("a", "kk"))


class Favorites(unittest.TestCase):
    def test_upgrade_without_snapshots_is_silent(self):
        old = {"day": "2026-10-30", "seenDeals": [], "seenTours": ["sweatberry_app-120"], "topics": {}}
        pushes, state, _ = plan(None, docs(tour(seats=3)), old, at(12))
        self.assertEqual(fav_pushes(pushes), [])
        self.assertEqual(state["tourSnap"]["sweatberry_app-120"]["seats"], 3)

    def test_few_seats_left(self):
        state = state_with(tour(seats=8))
        pushes, state, _ = plan(None, docs(tour(seats=4)), state, at(13))
        fav = fav_pushes(pushes)
        self.assertEqual(len(fav), 2)  # ru и kk
        self.assertEqual({p.topics[0] for p in fav},
                         {tour_topic("sweatberry_app-120", "ru"), tour_topic("sweatberry_app-120", "kk")})
        ru = next(p for p in fav if p.topics[0] == tour_topic("sweatberry_app-120", "ru"))
        self.assertIn("Осталось 4 места", ru.title)
        self.assertEqual(ru.data["signatureID"], "sweatberry_app-120")

    def test_many_seats_is_silent(self):
        state = state_with(tour(seats=12))
        pushes, _, _ = plan(None, docs(tour(seats=9)), state, at(13))
        self.assertEqual(fav_pushes(pushes), [])

    def test_once_a_day_then_next_day(self):
        state = state_with(tour(seats=8))
        pushes, state, _ = plan(None, docs(tour(seats=5)), state, at(13))
        self.assertEqual(len(fav_pushes(pushes)), 2)
        pushes, state, _ = plan(None, docs(tour(seats=3)), state, at(15))
        self.assertEqual(fav_pushes(pushes), [])          # сегодня уже писали
        pushes, _, _ = plan(None, docs(tour(seats=3)), state, at(10, day=31))
        self.assertEqual(len(fav_pushes(pushes)), 2)      # утром — новость про 3 места

    def test_price_drop(self):
        state = state_with(tour(price=4500))
        pushes, _, _ = plan(None, docs(tour(price=3900)), state, at(13))
        fav = fav_pushes(pushes)
        self.assertEqual(len(fav), 2)
        ru = next(p for p in fav if p.topics[0] == tour_topic("sweatberry_app-120", "ru"))
        self.assertIn("Цена снизилась", ru.title)
        self.assertIn("$3\u00a0900", ru.body)
        self.assertIn("$4\u00a0500", ru.body)

    def test_price_rise_is_silent(self):
        state = state_with(tour(price=3900))
        pushes, _, _ = plan(None, docs(tour(price=4500)), state, at(13))
        self.assertEqual(fav_pushes(pushes), [])

    def test_night_waits(self):
        state = state_with(tour(seats=8))
        pushes, state, _ = plan(None, docs(tour(seats=4)), state, at(23))
        self.assertEqual(fav_pushes(pushes), [])
        pushes, _, _ = plan(None, docs(tour(seats=4)), state, at(9, day=31))
        self.assertEqual(len(fav_pushes(pushes)), 2)

    def test_sold_out_is_silent(self):
        state = state_with(tour(seats=3))
        pushes, _, _ = plan(None, docs(tour(seats=1, sold=True)), state, at(13))
        self.assertEqual(fav_pushes(pushes), [])

    def test_kazakh_text(self):
        title, body = favorite_message(tour(seats=2), "seats", "kk")
        self.assertIn("2 орын қалды", title)
        self.assertIn("бастап", body)


if __name__ == "__main__":
    unittest.main()
