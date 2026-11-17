import unittest
from unittest.mock import patch
from fastapi import HTTPException
from alart.pricing import quote
from alart.stars_pricing import toman_to_stars

class Cursor:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value

class FakeDB:
    def __init__(self,custom=False):
        self.plan={"id":1,"service_id":2,"category":"volume_timed","is_custom":custom,"duration_hours":720,"traffic_gb":50,"user_limit":1,"price_minor":100000,"stars_price":100,"usd_price_cents":500}
        self.rule={"min_users":1,"max_users":20,"min_traffic_gb":10,"max_traffic_gb":500,"min_days":1,"max_days":365,"base_minor":1000,"per_user_minor":100,"per_gb_minor":10,"per_day_minor":20,"base_stars":5,"per_user_stars":1,"per_gb_stars":2,"per_day_stars":1,"base_usd_cents":100,"per_user_usd_cents":10,"per_gb_usd_cents":5,"per_day_usd_cents":2}
    def execute(self,sql,params):
        if "FROM plans p" in sql:return Cursor(self.plan)
        if "FROM pricing_rules" in sql:return Cursor(self.rule)
        if "FROM coupons" in sql:return Cursor({"id":9,"discount_percent":10})
        raise AssertionError(sql)

class PricingTests(unittest.TestCase):
    def test_fixed_and_coupon(self):
        result=quote(FakeDB(),plan_id=1,payment_method="stars",coupon_code="SAVE")
        self.assertEqual(result.final_minor,90)
        self.assertEqual(result.currency,"XTR")
        self.assertEqual(result.selection["traffic_gb"],50)

    def test_custom_formula(self):
        result=quote(FakeDB(custom=True),plan_id=1,payment_method="wallet",users=5,traffic_gb=100,duration_days=30)
        self.assertEqual(result.final_minor,1000+4*100+100*10+30*20)

    def test_custom_limits_enforced(self):
        with self.assertRaises(HTTPException):
            quote(FakeDB(custom=True),plan_id=1,payment_method="wallet",users=21,traffic_gb=100,duration_days=30)

    def test_fixed_rejects_customer_price_parameters(self):
        with self.assertRaises(HTTPException):
            quote(FakeDB(),plan_id=1,payment_method="wallet",users=10)

    def test_zero_wallet_quote_carries_explicit_free_flag(self):
        db=FakeDB()
        db.plan["price_minor"]=0
        db.plan["free_purchase_enabled"]=True
        result=quote(db,plan_id=1,payment_method="wallet")
        self.assertEqual(result.final_minor,0)
        self.assertTrue(result.free_purchase_enabled)

    def test_zero_stars_cannot_create_invoice(self):
        db=FakeDB()
        db.plan["stars_price"]=0
        with self.assertRaises(HTTPException):
            quote(db,plan_id=1,payment_method="stars")

    def test_live_stars_quote_uses_toman_price_and_rounds_up(self):
        config={"irr_per_usd":__import__('decimal').Decimal('1000000'),"stars_per_usd":__import__('decimal').Decimal('100'),"markup_percent":__import__('decimal').Decimal('0'),"source":"test","updated_at":"2026-09-18T00:00:00Z"}
        with patch('alart.pricing.stars_settings',return_value=config):
            result=quote(FakeDB(),plan_id=1,payment_method="stars",coupon_code="SAVE")
        # 100,000 IRR => 10 stars; the 10% coupon is applied before XTR conversion.
        self.assertEqual((result.listed_minor,result.discount_minor,result.final_minor),(10,1,9))
        self.assertEqual((result.toman_listed_minor,result.toman_discount_minor,result.toman_final_minor),(100000,10000,90000))
        self.assertEqual(result.pricing_reference["source"],"test")

    def test_operator_usd_price_per_star_is_inverted_without_float_rounding(self):
        from decimal import Decimal
        config={"irr_per_usd":Decimal('1000000'),"stars_per_usd":Decimal(1)/Decimal('0.0125'),"markup_percent":Decimal('0')}
        self.assertEqual(toman_to_stars(125000,config),10)

    def test_zero_limits_mean_unlimited_when_configured(self):
        db=FakeDB(custom=True)
        db.plan["category"]="unlimited"
        db.rule["min_users"]=0
        db.rule["min_days"]=0
        result=quote(db,plan_id=1,payment_method="wallet",users=0,duration_days=0)
        self.assertEqual(result.selection["users"],0)
        self.assertEqual(result.selection["duration_days"],0)
        self.assertEqual(result.final_minor,db.rule["base_minor"])

    def test_timed_volume_rejects_no_expiration(self):
        db=FakeDB(custom=True)
        db.rule["min_days"]=0
        with self.assertRaises(HTTPException):
            quote(db,plan_id=1,payment_method="wallet",users=1,traffic_gb=50,duration_days=0)

if __name__=="__main__": unittest.main()
