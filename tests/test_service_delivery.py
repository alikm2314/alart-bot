import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from alart.service_delivery import service_message

class ServiceDeliveryTests(unittest.TestCase):
    def test_expired_trial_replaces_guide_with_delete_and_navigation(self):
        text,markup=service_message({'id':8,'name':'Test','client_name':'trial','status':'expired','price_minor':0},'en')
        actions=[button.get('callback_data') for row in markup['inline_keyboard'] for button in row]
        self.assertIn('service:archive:8',actions)
        self.assertIn('menu:services',actions)
        self.assertIn('menu:home',actions)
        self.assertNotIn('Connection guide',str(markup))

    def test_active_service_keeps_connection_guide(self):
        with patch('alart.service_delivery.settings',return_value={'DOMAIN':'example.test'}):
            _,markup=service_message({'id':8,'name':'Paid','client_name':'paid','status':'active','price_minor':100,
                'expires_at':datetime.now(timezone.utc)+timedelta(days=1),'traffic_limit_bytes':None,'traffic_used_bytes':None},'en')
        self.assertIn('Connection guide',str(markup))

if __name__ == '__main__': unittest.main()
