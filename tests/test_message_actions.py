import unittest
from unittest.mock import patch
from alart.message_actions import markup

class MessageActionTests(unittest.TestCase):
    def test_only_fixed_miniapp_destinations_are_rendered(self):
        with patch('alart.message_actions.settings',return_value={'DOMAIN':'example.test'}):
            result=markup([{'label':'Buy','surface':'miniapp','action':'buy','style':'success','coupon_code':'save_25'},{'label':'My services','surface':'chat','action':'services','style':'danger'},{'label':'Nope','action':'url'}])
        self.assertEqual(result['inline_keyboard'][0][0]['web_app']['url'],'https://example.test/app/?page=services&coupon=SAVE_25')
        self.assertEqual(result['inline_keyboard'][1][0]['callback_data'],'menu:services')
        self.assertEqual(result['inline_keyboard'][0][0]['style'],'success')
        self.assertEqual(result['inline_keyboard'][1][0]['style'],'danger')
        self.assertEqual(len(result['inline_keyboard']),2)

if __name__ == '__main__': unittest.main()
