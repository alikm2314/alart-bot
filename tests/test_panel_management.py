import unittest
from pathlib import Path

from alart.panel_management import sync_inbound_snapshot

ROOT=Path(__file__).resolve().parents[1]

class Result:
    def __init__(self, rowcount=0): self.rowcount=rowcount

class Connection:
    def __init__(self): self.calls=[]
    def execute(self, query, params):
        self.calls.append((" ".join(query.split()),params))
        return Result(2)

class InboundSnapshotTests(unittest.TestCase):
    def test_snapshot_upserts_in_panel_order_then_removes_missing_rows(self):
        conn=Connection()
        removed=sync_inbound_snapshot(conn,7,[
            {"remote_id":42,"remark":"first","protocol":"vless","remote_enabled":True},
            {"remote_id":3,"remark":"second","protocol":"trojan","remote_enabled":False},
        ])
        self.assertEqual(removed,2)
        self.assertEqual(conn.calls[0][1],(7,42,"first","vless",True,0))
        self.assertEqual(conn.calls[1][1],(7,3,"second","trojan",False,1))
        self.assertIn("DELETE FROM panel_inbounds",conn.calls[2][0])
        self.assertEqual(conn.calls[2][1],(7,[42,3]))

    def test_empty_successful_snapshot_removes_every_inbound(self):
        conn=Connection()
        sync_inbound_snapshot(conn,7,[])
        self.assertEqual(len(conn.calls),1)
        self.assertEqual(conn.calls[0][1],(7,))

    def test_archived_products_are_not_returned_to_the_management_catalog(self):
        routes=(ROOT/'alart'/'panel_routes.py').read_text(encoding='utf-8')
        self.assertIn('FROM services WHERE NOT internal AND enabled ORDER BY id',routes)
        self.assertIn('WHERE p.enabled AND s.enabled AND NOT s.internal ORDER BY p.id',routes)

if __name__ == "__main__": unittest.main()
