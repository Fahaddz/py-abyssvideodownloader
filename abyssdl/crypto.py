from __future__ import annotations

import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Util import Counter


def get_key(value) -> str:
    """MD5 hash compatible with the Kotlin CryptoHelper.getKey behavior."""
    if isinstance(value, (int, float)):
        ba = bytearray()
        for ch in str(value):
            ba.append(int(ch) if ch.isdigit() else ord(ch) & 0xFF)
        data = bytes(ba)
    else:
        data = str(value).encode("utf-8")
    return hashlib.md5(data).hexdigest()


def _aes_ctr_cipher(key_hex: str):
    key_bytes = key_hex.encode("utf-8")
    iv_int = int.from_bytes(key_bytes[:16], "big")
    ctr = Counter.new(128, initial_value=iv_int, little_endian=False)
    return AES.new(key_bytes, AES.MODE_CTR, counter=ctr)


def aes_ctr_encrypt(plaintext: str, key_hex: str) -> str:
    cipher = _aes_ctr_cipher(key_hex)
    return cipher.encrypt(plaintext.encode("utf-8")).decode("iso-8859-1")


def aes_ctr_decrypt_str(ciphertext_str: str, key_hex: str) -> str:
    cipher = _aes_ctr_cipher(key_hex)
    plaintext = cipher.decrypt(ciphertext_str.encode("iso-8859-1"))
    return plaintext.decode("utf-8")


def double_base64_encode(encrypted_iso_str: str) -> str:
    raw = encrypted_iso_str.encode("iso-8859-1")
    first = base64.b64encode(raw).decode("utf-8").replace("=", "")
    return base64.b64encode(first.encode("utf-8")).decode("utf-8").replace("=", "")
