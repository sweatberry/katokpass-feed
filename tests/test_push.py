"""Правила пушей: без сети и без Firebase — проверяем только, что и кому решено отправить."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

from push import flight_topic, plan, tour_dates, deal_message, tour_message  # noqa: E402

ALMATY = timezone(timedelta(hours=5))


def at(hour: int, day: int = 30) -> datetime:
    return datetime(2026, 10, day, hour, 5, tzinfo=ALMATY)


def deal(id_: str, price: int, to: str = "Анталия", country: str = "Турция", origin: str = "Алматы") -> dict:
    return {"id": id_, "from": origin, "to": to, "country": country, "toCountry": country,
            "departure": "2026-11-12", "returnDate": None, "price": price, "trip": "roundTrip",
            "airline": "SCAT", "seats": 3}


def feed(*deals) -> dict:
    return {"deals": list(deals)}


def topics_of(pushes) -> set:
    return {t for p in pushes for t in p.topics}


EMPTY = {"day": "2026-10-30", "seenDeals": [], "seenTours": [], "topics": {}}


class TopicName(unittest.TestCase):
    def test_known_value(self):
        # То же значение проверяет приложение (PushTopics.flight) — формулы должны совпадать.
        self.assertEqual(flight_topic("Турция", "Алматы", 120000, "ru", "i"), "f_fa063bece8803c44763c77ef")

    def test_valid_for_fcm(self):
        t = flight_topic("Южная Корея", None, 500000, "kk", "e")
        self.assertRegex(t, r"^[a-zA-Z0-9_]+$")


class FirstRun(unittest.TestCase):
    def test_remembers_without_sending(self):
        pushes, state, _ = plan(feed(deal("a", 80000)), {"tours": [{"id": "9", "title": "Япония"}]}, None, at(12))
        self.assertEqual(pushes, [])
        self.assertEqual((state["seenDeals"], state["seenTours"]), (["a"], ["9"]))


class Deals(unittest.TestCase):
    def test_goes_to_topics_with_threshold_above_price(self):
        pushes, _, _ = plan(feed(deal("a", 89000)), None, EMPTY, at(12))
        sent = topics_of(pushes)
        self.assertIn(flight_topic("Турция", "Алматы", 90000, "ru", "i"), sent)
        self.assertIn(flight_topic("Турция", None, 500000, "kk", "i"), sent)
        self.assertNotIn(flight_topic("Турция", "Алматы", 80000, "ru", "i"), sent)
        self.assertNotIn(flight_topic("Турция", "Астана", 90000, "ru", "i"), sent)

    def test_conditions_have_at_most_five_topics(self):
        pushes, _, _ = plan(feed(deal("a", 20000)), None, EMPTY, at(12))
        self.assertTrue(all(1 <= len(p.topics) <= 5 for p in pushes))
        self.assertIn("condition", pushes[0].fcm()["message"])

    def test_evening_mode_gets_only_super_cheap(self):
        pushes, _, _ = plan(feed(deal("a", 89000)), None, EMPTY, at(12))
        sent = topics_of(pushes)
        self.assertNotIn(flight_topic("Турция", "Алматы", 150000, "ru", "e"), sent)   # 89×2 > 150
        self.assertIn(flight_topic("Турция", "Алматы", 200000, "ru", "e"), sent)      # 89×2 ≤ 200

    def test_one_instant_per_topic_per_day_rest_to_digest(self):
        pushes, state, _ = plan(feed(deal("a", 89000), deal("b", 95000)), None, EMPTY, at(12))
        topic = flight_topic("Турция", "Алматы", 120000, "ru", "i")
        self.assertEqual(sum(topic in p.topics for p in pushes), 1)
        self.assertEqual(state["topics"][topic]["held"], ["b"])

        # в 19:00 — сводка с отложенным рейсом
        pushes, state, _ = plan(feed(deal("a", 89000), deal("b", 95000)), None, state, at(19))
        digest = [p for p in pushes if topic in p.topics]
        self.assertEqual(len(digest), 1)
        self.assertIn("Турция: 1 новый рейс", digest[0].title)
        self.assertTrue(state["topics"][topic]["digest"])

        # второй раз сводка в тот же день не приходит
        pushes, _, _ = plan(feed(deal("a", 89000), deal("b", 95000)), None, state, at(20))
        self.assertEqual(pushes, [])

    def test_night_waits_for_morning(self):
        pushes, state, _ = plan(feed(deal("a", 89000)), None, EMPTY, at(23))
        self.assertEqual(pushes, [])
        self.assertEqual(state["seenDeals"], [])
        pushes, state, _ = plan(feed(deal("a", 89000)), None, {**state, "day": "2026-10-30"}, at(9, day=31))
        self.assertTrue(pushes)
        self.assertEqual(state["seenDeals"], ["a"])

    def test_new_day_resets_limits(self):
        _, state, _ = plan(feed(deal("a", 89000)), None, EMPTY, at(12))
        pushes, _, _ = plan(feed(deal("a", 89000), deal("b", 95000)), None, state, at(10, day=31))
        self.assertIn(flight_topic("Турция", "Алматы", 120000, "ru", "i"), topics_of(pushes))

    def test_same_deal_not_sent_twice(self):
        _, state, _ = plan(feed(deal("a", 89000)), None, EMPTY, at(12))
        pushes, _, _ = plan(feed(deal("a", 89000)), None, state, at(13))
        self.assertEqual(pushes, [])

    def test_collapse_id_per_deal(self):
        pushes, _, _ = plan(feed(deal("a", 89000)), None, EMPTY, at(12))
        ids = {p.fcm()["message"]["apns"]["headers"]["apns-collapse-id"] for p in pushes}
        self.assertEqual(ids, {"deal-a"})


class Texts(unittest.TestCase):
    def test_deal_ru_and_kk(self):
        self.assertEqual(deal_message(deal("a", 89000), "ru"),
                         ("Алматы → Анталия, 89 000 ₸",
                          "Вылет 12 ноября, туда-обратно, SCAT. Осталось 3 места."))
        title, body = deal_message(deal("a", 89000), "kk")
        self.assertEqual(title, "Алматы → Анталья, 89 000 ₸")
        self.assertTrue(body.startswith("Ұшу 12 қараша, барып-қайту"))

    def test_tour_dates_like_app(self):
        t = {"departure": "2026-11-12", "returnDate": "2026-11-19"}
        self.assertEqual(tour_dates(t, "ru"), "с 12 по 19 ноября")
        self.assertEqual(tour_dates({**t, "explicitYear": True}, "ru"), "с 12 по 19 ноября 2026")
        self.assertEqual(tour_dates({"departure": "2026-11-20", "returnDate": "2026-12-02"}, "ru"),
                         "с 20 ноября по 2 декабря")
        self.assertEqual(tour_dates(t, "kk"), "12–19 қараша")

    def test_tour_message(self):
        title, body = tour_message({"id": "9", "title": "Япония, Южная Корея", "flags": ["🇯🇵", "🇰🇷"],
                                    "departure": "2027-04-03", "nights": 7,
                                    "priceFrom": 1690000, "currency": "₸"}, "ru")
        self.assertEqual(title, "🇯🇵🇰🇷 Новый авторский тур: Япония, Южная Корея")
        self.assertEqual(body, "с 3 по 10 апреля · от 1 690 000 ₸ за человека")


class Tours(unittest.TestCase):
    TOUR = {"id": "15", "title": "Исландия", "flags": ["🇮🇸"], "departure": "2027-02-07",
            "nights": 9, "priceFrom": 3000, "currency": "$"}

    def test_new_tour_goes_to_both_languages(self):
        pushes, state, _ = plan(None, {"tours": [self.TOUR]}, EMPTY, at(12))
        self.assertEqual(sorted(p.topics[0] for p in pushes), ["tours_kk", "tours_ru"])
        self.assertEqual(pushes[0].data, {"signatureID": "15"})
        self.assertEqual(state["seenTours"], ["15"])

    def test_one_tour_per_run(self):
        second = {**self.TOUR, "id": "16", "title": "Бразилия"}
        pushes, state, _ = plan(None, {"tours": [self.TOUR, second]}, EMPTY, at(12))
        self.assertEqual(len(pushes), 2)
        pushes, state, _ = plan(None, {"tours": [self.TOUR, second]}, state, at(12))
        self.assertEqual(len(pushes), 2)
        self.assertIn("Бразилия", pushes[0].title)

    def test_sold_out_tour_is_silent(self):
        pushes, state, _ = plan(None, {"tours": [{**self.TOUR, "isSoldOut": True}]}, EMPTY, at(12))
        self.assertEqual(pushes, [])
        self.assertEqual(state["seenTours"], ["15"])

    def test_tour_at_night_waits(self):
        pushes, state, _ = plan(None, {"tours": [self.TOUR]}, EMPTY, at(3))
        self.assertEqual(pushes, [])
        self.assertEqual(state["seenTours"], [])


if __name__ == "__main__":
    unittest.main()
