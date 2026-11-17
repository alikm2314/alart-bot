import unittest
from unittest.mock import patch

from alart import referral


class Result:
    def __init__(self, one=None, many=None): self.one,self.many=one,many or []
    def fetchone(self): return self.one
    def fetchall(self): return self.many


class ReferralConnection:
    def __init__(self, order_status="active", payment_status="confirmed"):
        self.order={"id":17,"user_id":20,"status":order_status,"price_minor":1000,
                    "renewal_of_sub_id":None,"payment_status":payment_status}
        self.referral={"referred_user_id":20,"referrer_user_id":10,"status":"pending"}
        self.calls=[]

    def execute(self, query, params=None):
        self.calls.append((query,params))
        if "FROM orders o JOIN payments" in query: return Result(self.order)
        if "FROM referrals WHERE referred_user_id" in query: return Result(self.referral)
        if "FROM orders WHERE user_id" in query: return Result()
        if "FROM referral_settings" in query: return Result({"enabled":True,"referrer_reward_minor":500000,"referred_reward_minor":300000})
        if "SELECT language FROM users" in query: return Result({"language":"fa"})
        if "UPDATE referrals SET status='awarded'" in query:
            self.referral["status"]="awarded"
            return Result()
        return Result()


class ReferralTests(unittest.TestCase):
    def test_code_is_stable_and_does_not_reveal_user_id(self):
        with patch.object(referral,"settings",return_value={"SESSION_SECRET":"secret"}):
            code=referral.code_for(12345)
            self.assertEqual(code,referral.code_for(12345))
            self.assertNotEqual(code,referral.code_for(12346))
        self.assertEqual(len(code),16)
        self.assertNotIn("12345",code)

    def test_only_confirmed_active_first_order_awards_once(self):
        conn=ReferralConnection(payment_status="pending")
        with patch.object(referral,"ledger") as credit:
            self.assertFalse(referral.award(conn,17))
            credit.assert_not_called()
            conn.order["payment_status"]="confirmed"
            self.assertTrue(referral.award(conn,17))
            self.assertFalse(referral.award(conn,17))
            self.assertEqual(credit.call_count,2)
        self.assertEqual(conn.referral["status"],"awarded")

    def test_invalid_referral_code_never_claims(self):
        conn=ReferralConnection()
        self.assertFalse(referral.claim(conn,20,"../../bad"))
        self.assertEqual(conn.calls,[])


if __name__=="__main__": unittest.main()
