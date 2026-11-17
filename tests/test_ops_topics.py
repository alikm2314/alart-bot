import os
import unittest
from unittest.mock import patch
from alart.ops_topics import _destination,emit,EVENTS,TOPIC_TITLES
from alart import bot


class Connection:
    def __init__(self): self.calls=[]
    def execute(self,sql,params):
        self.calls.append((sql,params))
        class Result:
            def fetchall(self): return []
        return Result()


class TopicTests(unittest.TestCase):
    def test_all_categories_have_configurable_topic(self):
        categories={value[0] for value in EVENTS.values()}
        self.assertEqual(categories,{"PURCHASES","PAYMENTS","WALLET","REFERRAL","USERS","PROVISIONING","ERRORS","SECURITY","BACKUP","SYSTEM","FINANCE"})
        self.assertEqual(set(TOPIC_TITLES),categories)

    def test_uses_private_chat_and_topic_identifiers(self):
        with patch.dict(os.environ,{"OPS_CHAT_ID":"-1001234567890","OPS_TOPIC_PAYMENTS":"23"}):
            self.assertEqual(_destination("payment.confirmed"),(-1001234567890,23))

    def test_event_contains_only_controlled_type_and_id(self):
        conn=Connection()
        emit(conn,"payment.confirmed",42)
        self.assertEqual(conn.calls[0][1],("payment.confirmed",42,"payment.confirmed:42"))

    def test_topic_titles_are_short_emoji_labels(self):
        self.assertEqual(TOPIC_TITLES["FINANCE"],"📈 Finance")
        self.assertFalse(any(title.startswith("Alart") for title in TOPIC_TITLES.values()))

    def test_topic_setup_requires_forum_and_persists_confirmed_ids(self):
        calls=[]
        def fake_api(method,payload):
            calls.append((method,payload))
            if method=="getChat": return {"is_forum":True}
            return {"message_thread_id":len(calls)+100}
        class Tx:
            def __enter__(self): return Connection()
            def __exit__(self,*args): return False
        with patch.object(bot,"api",side_effect=fake_api),patch.object(bot,"transaction",return_value=Tx()):
            result=bot.setup_topics(-1001234567890)
        self.assertEqual(set(result),set(TOPIC_TITLES))
        self.assertEqual(calls[0][0],"getChat")
        self.assertEqual(sum(method=="createForumTopic" for method,_ in calls),len(TOPIC_TITLES))


if __name__=="__main__": unittest.main()
