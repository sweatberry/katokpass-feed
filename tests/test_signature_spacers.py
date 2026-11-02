"""Посты с невидимой строкой «⠀» между списками и значком ☑ — как Бразилия и Кения в канале."""
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "collector"))

from signature_parser import parse  # noqa: E402

TODAY = date(2026, 11, 2)

KENYA = """🇰🇪 Кения - сердце дикой Африки!

Всю поездку нас будет сопровождать профессиональный фотограф.

🗓 Даты: с 10 по 18 января
💵 Стоимость: 2 090 000 ₸ 
Предоплата: 500 000 ₸ 

☑ В стоимость входят:

• Международные перелёты (вылет из Алматы) (багаж 20 кг, ручная кладь 5 кг);
• Трансферы из/в аэропорта;
• ⁠3-х разовое питание на сафари;
• Туристическая⁠ страховка;
⠀
⛔️ В стоимость не входят:

• Карманные расходы;
• Чаевые;
• Питание (за исключением сафари)
"""


class BrazilPost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tour = parse((HERE / "samples" / "signature_brazil.txt").read_text("utf-8"), TODAY, "53").tour

    def test_includes_are_a_list(self):
        inc = self.tour["includes"]
        self.assertEqual(len(inc), 8)
        self.assertTrue(inc[0].startswith("Все внутренние перелеты"))
        self.assertIn("Игуасу - Буэнос Айрес", inc[0])        # строка-продолжение — в том же пункте
        self.assertEqual(inc[-1], "Экскурсия по фавелам Рио")

    def test_excludes_are_a_list(self):
        exc = self.tour["excludes"]
        self.assertEqual(len(exc), 4)
        self.assertEqual(exc[1], "Карманные расходы")

    def test_lists_not_in_description(self):
        self.assertNotIn("В стоимость", self.tour["description"] or "")
        self.assertNotIn("Сопровождение", self.tour["description"] or "")

    def test_prepayment_and_variants(self):
        self.assertEqual(self.tour["prepayment"], "500 000 ₸")
        self.assertEqual(len(self.tour["variants"]), 2)


class KenyaPost(unittest.TestCase):
    def test_bullets_semicolons_and_invisible_chars(self):
        t = parse(KENYA, TODAY, "77").tour
        self.assertEqual(t["includes"], ["Международные перелёты (вылет из Алматы) (багаж 20 кг, ручная кладь 5 кг)",
                                         "Трансферы из/в аэропорта", "3-х разовое питание на сафари",
                                         "Туристическая страховка"])
        self.assertEqual(t["excludes"], ["Карманные расходы", "Чаевые", "Питание (за исключением сафари)"])


if __name__ == "__main__":
    unittest.main()
