"""Сбор авторских туров из канала — на подставном Telegram, без сети."""
import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "collector"))

import signature_collect as sc  # noqa: E402

AFRICA = (HERE / "samples" / "signature_africa.txt").read_text("utf-8")
ICELAND = (HERE / "samples" / "signature_iceland.txt").read_text("utf-8")
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


def msg(id, text="", grouped=None, reply=None, photo=None):
    return NS(id=id, message=text, date=NOW, grouped_id=grouped,
              reply_to=NS(reply_to_msg_id=reply) if reply else None,
              photo=NS(id=photo) if photo else None, document=None, file=None)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.downloads = 0

    async def iter_messages(self, entity, limit=None):
        for m in sorted(self.messages, key=lambda m: -m.id):   # как Telegram: от новых к старым
            yield m

    async def download_media(self, m, file=None):
        self.downloads += 1
        return b"\xff\xd8 fake jpeg " + str(m.photo.id).encode()


async def resolve(client, source):
    return NS(username="sweatberry_app")


def channel():
    return [
        # Африка: альбом без подписи + длинный текст ответом на альбом
        msg(10, grouped=777, photo=1001), msg(11, grouped=777, photo=1002), msg(12, grouped=777, photo=1003),
        msg(13, AFRICA, reply=10),
        # Исландия: альбом с подписью
        msg(20, ICELAND, grouped=888, photo=2001), msg(21, grouped=888, photo=2002),
        # Обычный пост — не тур, пропускаем молча
        msg(30, "Друзья, скоро анонс новых направлений!"),
        # Похоже на тур, но нет цены — бот должен сообщить
        msg(40, "🇯🇵 Япония: сакура\n\nДаты: с 3 по 14 апреля", photo=4001),
        # Тур, который уже улетел
        msg(50, "🇹🇷 Турция\n\nДаты: с 1 по 5 октября 2026\nСтоимость: $900", photo=5001),
    ]


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="katokpass-sig-"))
        sc.OUTPUT = self.tmp / "signature.json"
        sc.PHOTOS = self.tmp / "signature"
        sc.STATE = self.tmp / "signature_state.json"
        self.sent = []
        sc.notify = lambda text: self.sent.append(text)
        self.config = {"signatureChannel": "https://t.me/sweatberry_app", "adminChat": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_once(self, messages):
        self.sent.clear()
        client = FakeClient(messages)
        asyncio.run(sc.collect_signature(client, self.config, NOW, resolve))
        return client, json.loads(sc.OUTPUT.read_text("utf-8"))

    def test_full_cycle(self):
        client, data = self.run_once(channel())
        tours = {t["id"]: t for t in data["tours"]}

        # Два тура: Африка (текст ответом на альбом) и Исландия (подпись к альбому)
        self.assertEqual(set(tours), {"13", "20"})
        africa, iceland = tours["13"], tours["20"]
        self.assertEqual(africa["photos"], ["signature/1001.jpg", "signature/1002.jpg", "signature/1003.jpg"])
        self.assertEqual(iceland["photos"], ["signature/2001.jpg", "signature/2002.jpg"])
        self.assertEqual(africa["telegram"], "https://t.me/sweatberry_app/13")
        self.assertEqual((africa["priceFrom"], africa["currency"]), (4000, "$"))
        self.assertEqual(len(africa["variants"]), 2)
        self.assertEqual(africa["whatsApp"], "77787379977")
        for name in ("1001", "1002", "1003", "2001", "2002"):
            self.assertTrue((sc.PHOTOS / f"{name}.jpg").exists())
        self.assertFalse((sc.PHOTOS / "5001.jpg").exists())       # улетевший тур фото не качаем

        # Сортировка по дате вылета: Африка (ноябрь) раньше Исландии (февраль)
        self.assertEqual([t["id"] for t in data["tours"]], ["13", "20"])

        # Сообщения боту: два «опубликован» и одна ошибка про Японию
        published = [s for s in self.sent if "опубликован" in s]
        errors = [s for s in self.sent if "не попал" in s]
        self.assertEqual(len(published), 2)
        self.assertEqual(len(errors), 1)
        self.assertIn("Япония", errors[0])
        self.assertIn("цену", errors[0])
        self.assertFalse(any("анонс новых" in s for s in self.sent))  # обычный пост — тишина

        # Повторный запуск: ничего нового не шлём и фото заново не качаем
        client2, data2 = self.run_once(channel())
        self.assertEqual(self.sent, [])
        self.assertEqual(client2.downloads, 0)
        self.assertEqual(data2, data)

    def test_broken_edit_keeps_previous_version(self):
        self.run_once(channel())
        broken = [m if m.id != 13 else msg(13, AFRICA.replace("Стоимость", "Сколько стоит"), reply=10)
                  for m in channel()]
        _, data = self.run_once(broken)
        africa = next(t for t in data["tours"] if t["id"] == "13")
        self.assertEqual(africa["priceFrom"], 4000)                 # прежняя версия на месте
        self.assertTrue((sc.PHOTOS / "1001.jpg").exists())          # и её фото тоже
        self.assertEqual(len([s for s in self.sent if "прежняя версия" in s]), 1)
        self.assertFalse(any("опубликован" in s for s in self.sent))

    def test_deleted_post_removes_tour_and_photos(self):
        self.run_once(channel())
        without_iceland = [m for m in channel() if m.id not in (20, 21)]
        _, data = self.run_once(without_iceland)
        self.assertEqual([t["id"] for t in data["tours"]], ["13"])
        self.assertFalse((sc.PHOTOS / "2001.jpg").exists())

    def test_sold_out(self):
        msgs = [m if m.id != 13 else msg(13, AFRICA + "\n#набрано", reply=10) for m in channel()]
        _, data = self.run_once(msgs)
        self.assertTrue(next(t for t in data["tours"] if t["id"] == "13")["isSoldOut"])


class LinkingTests(unittest.TestCase):
    """Как текст привязывается к фото."""

    def M(self, id, text="", grouped=None, reply=None, photo=None):
        return sc.Msg(id=id, text=text, date=NOW, grouped_id=grouped, reply_to=reply, photo_id=photo)

    def test_reply_to_any_photo_of_album_takes_whole_album(self):
        posts = sc.build_posts([self.M(1, grouped=5, photo=11), self.M(2, grouped=5, photo=12),
                                self.M(3, "текст", reply=2)])
        self.assertEqual(len(posts), 1)
        self.assertEqual([p.photo_id for p in posts[0].photos], [11, 12])

    def test_reply_wins_over_short_caption(self):
        posts = sc.build_posts([self.M(1, "коротко", grouped=5, photo=11), self.M(2, grouped=5, photo=12),
                                self.M(3, "полный текст", reply=1)])
        self.assertEqual([p.text_msg.text for p in posts], ["полный текст"])

    def test_text_without_photos(self):
        posts = sc.build_posts([self.M(1, "только текст")])
        self.assertEqual((len(posts), posts[0].photos), (1, []))

    def test_text_right_after_album_without_reply(self):
        # Так выложен первый тур в канале: альбом из 7 фото, следом отдельное сообщение с текстом
        from datetime import timedelta
        album = [sc.Msg(id=i, text="", date=NOW, grouped_id=5, photo_id=100 + i) for i in range(2, 9)]
        text = sc.Msg(id=9, text="полный текст", date=NOW + timedelta(minutes=1))
        posts = sc.build_posts(album + [text])
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].text_msg.id, 9)
        self.assertEqual(len(posts[0].photos), 7)

    def test_text_long_after_album_is_separate(self):
        from datetime import timedelta
        posts = sc.build_posts([self.M(1, grouped=5, photo=11),
                                sc.Msg(id=2, text="через два часа", date=NOW + timedelta(hours=2))])
        self.assertEqual([(p.text_msg.id, len(p.photos)) for p in posts], [(2, 0)])

    def test_text_after_captioned_album_is_separate(self):
        posts = sc.build_posts([self.M(1, "своя подпись", grouped=5, photo=11), self.M(2, "отдельный пост")])
        self.assertEqual(sorted((p.text_msg.id, len(p.photos)) for p in posts), [(1, 1), (2, 0)])

    def test_single_photo_with_caption(self):
        posts = sc.build_posts([self.M(1, "подпись", photo=9)])
        self.assertEqual([p.photo_id for p in posts[0].photos], [9])


if __name__ == "__main__":
    unittest.main()
