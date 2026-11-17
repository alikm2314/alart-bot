"""Streaming authenticated encryption for local backup archives.

The restore script stages and verifies the complete plaintext before using it.
"""
import base64
import os
import sys
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"ALART-BACKUP-v1\n"
CHUNK = 1024 * 1024


def key():
    raw = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
    try:
        value = base64.urlsafe_b64decode(raw.encode())
    except (ValueError, base64.binascii.Error) as exc:
        raise RuntimeError("Invalid BACKUP_ENCRYPTION_KEY") from exc
    if len(value) != 32:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY must contain 32 random bytes")
    return value


def encrypt(source, destination, secret):
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(secret), modes.GCM(nonce)).encryptor()
    destination.write(MAGIC + nonce)
    while block := source.read(CHUNK):
        destination.write(encryptor.update(block))
    destination.write(encryptor.finalize())
    destination.write(encryptor.tag)


def decrypt(source, destination, secret):
    if source.read(len(MAGIC)) != MAGIC:
        raise ValueError("Invalid backup format")
    nonce = source.read(12)
    if len(nonce) != 12:
        raise ValueError("Truncated backup")
    decryptor = Cipher(algorithms.AES(secret), modes.GCM(nonce)).decryptor()
    tail = b""
    while block := source.read(CHUNK):
        tail += block
        if len(tail) > 16:
            body, tail = tail[:-16], tail[-16:]
            destination.write(decryptor.update(body))
    if len(tail) != 16:
        raise ValueError("Truncated backup")
    destination.write(decryptor.finalize_with_tag(tail))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("encrypt", "decrypt"):
        raise SystemExit("Usage: python -m alart.backup_crypto encrypt|decrypt")
    try:
        globals()[sys.argv[1]](sys.stdin.buffer, sys.stdout.buffer, key())
    except Exception as exc:
        print(f"Backup cryptography failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
