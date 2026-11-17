import unittest
from alart.money import toman, rial_from_toman


class MoneyTests(unittest.TestCase):
    def test_integer_conversion_and_legacy_remainder(self):
        self.assertEqual(toman(500000), "50,000 تومان")
        self.assertEqual(toman(12345), "1,234.5 تومان")
        self.assertEqual(toman(-15), "−1.5 تومان")
        self.assertEqual(rial_from_toman(50000), 500000)
