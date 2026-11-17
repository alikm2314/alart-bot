import unittest
from fastapi import HTTPException
from alart.qr import connection_png, first_connection_link


class QrTests(unittest.TestCase):
    def test_png_contains_only_first_link(self):
        config="vless://secret@host:443\nvless://other@host:443"
        self.assertEqual(first_connection_link(config),"vless://secret@host:443")
        self.assertTrue(connection_png(config).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_unsupported_or_oversized_link(self):
        for value in ("", "javascript:alert(1)","vless://"+"x"*3000):
            with self.assertRaises(HTTPException): first_connection_link(value)


if __name__ == "__main__": unittest.main()
