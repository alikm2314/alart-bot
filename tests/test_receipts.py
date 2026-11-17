import unittest
from fastapi import HTTPException
from alart.payment_routes import _valid_receipt, MAX_RECEIPT_BYTES


class ReceiptValidationTests(unittest.TestCase):
    def test_accepts_supported_signatures(self):
        for body, kind in ((b"\xff\xd8\xffpayload", "image/jpeg"),
                           (b"\x89PNG\r\n\x1a\npayload", "image/png"),
                           (b"RIFFxxxxWEBPpayload", "image/webp")):
            self.assertIsNone(_valid_receipt(body, kind))

    def test_rejects_mismatched_or_oversized_content(self):
        for body, kind in ((b"<script>", "image/png"),
                           (b"RIFFxxxxNOPE", "image/webp"),
                           (b"\xff\xd8\xff", "application/pdf"),
                           (b"x" * (MAX_RECEIPT_BYTES + 1), "image/jpeg")):
            with self.assertRaises(HTTPException):
                _valid_receipt(body, kind)


if __name__ == "__main__":
    unittest.main()
