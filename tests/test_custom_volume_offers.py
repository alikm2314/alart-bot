import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class CustomVolumeOfferContractTests(unittest.TestCase):
    def test_schema_keeps_custom_offers_internal_and_state_is_constrained(self):
        schema=(ROOT/"db"/"024_custom_volume_offers.sql").read_text(encoding="utf-8")
        state=(ROOT/"db"/"025_custom_volume_bot_state.sql").read_text(encoding="utf-8")
        self.assertIn("internal boolean",schema)
        self.assertIn("custom_volume_offers",schema)
        self.assertIn("custom_volume_amount",state)

    def test_public_catalog_and_bot_do_not_expose_internal_offer_as_ready_product(self):
        api=(ROOT/"alart"/"api.py").read_text(encoding="utf-8")
        bot=(ROOT/"alart"/"bot.py").read_text(encoding="utf-8")
        self.assertIn("WHERE enabled AND NOT internal",api)
        self.assertIn("NOT s.internal",bot)
        self.assertIn("buy:custom:",bot)

    def test_custom_volume_input_is_bounded_to_positive_whole_gigabytes(self):
        bot=(ROOT/"alart"/"bot.py").read_text(encoding="utf-8")
        self.assertIn("gb<1 or gb>500",bot)
        self.assertIn("str(gb)!=raw_amount",bot)

if __name__ == "__main__":
    unittest.main()
