import unittest
from unittest.mock import patch
from alart.bot import handle


class BotPrivacyTests(unittest.TestCase):
    def test_customer_account_command_in_group_does_not_touch_database(self):
        update={"message":{"from":{"id":123},"chat":{"id":-100456,"type":"supergroup"},"text":"/wallet"}}
        with patch("alart.bot.transaction",side_effect=AssertionError("group command reached database")):
            self.assertIsNone(handle(update))


if __name__=="__main__": unittest.main()
