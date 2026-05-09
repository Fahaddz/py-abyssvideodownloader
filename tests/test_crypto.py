import hashlib

from abyssdl.crypto import aes_ctr_decrypt_str, aes_ctr_encrypt, double_base64_encode, get_key


def test_get_key_matches_string_md5():
    assert get_key("1:slug:2") == hashlib.md5(b"1:slug:2").hexdigest()


def test_get_key_matches_numeric_byte_behavior():
    assert get_key(123) == hashlib.md5(bytes([1, 2, 3])).hexdigest()


def test_aes_ctr_round_trip():
    key = get_key("media-key")
    encrypted = aes_ctr_encrypt("/mp4/1/2/3/2097152/0", key)
    assert aes_ctr_decrypt_str(encrypted, key) == "/mp4/1/2/3/2097152/0"


def test_double_base64_removes_padding():
    token = double_base64_encode("abc")
    assert "=" not in token
    assert token == "WVdKag"
