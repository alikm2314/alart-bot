import io
import os
import unittest
from alart.backup_crypto import encrypt, decrypt


class BackupCryptoTests(unittest.TestCase):
    def test_round_trip_and_tamper_detection(self):
        secret = os.urandom(32)
        original = os.urandom(1024 * 1024 + 17)
        sealed = io.BytesIO()
        encrypt(io.BytesIO(original), sealed, secret)
        output = io.BytesIO()
        decrypt(io.BytesIO(sealed.getvalue()), output, secret)
        self.assertEqual(output.getvalue(), original)
        tampered = bytearray(sealed.getvalue())
        tampered[30] ^= 1
        with self.assertRaises(Exception):
            decrypt(io.BytesIO(tampered), io.BytesIO(), secret)

    def test_truncation_rejected(self):
        secret = os.urandom(32)
        with self.assertRaises(ValueError):
            decrypt(io.BytesIO(b"ALART-BACKUP-v1\n"), io.BytesIO(), secret)
