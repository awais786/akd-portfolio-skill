import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from akd.protocol.crypto import encrypt_login_field, load_public_key

KEY = Path(__file__).resolve().parents[1] / "src" / "akd" / "public.key"


def test_load_public_key_is_512_bit_e_65537():
    pk = load_public_key(KEY)
    nums = pk.public_numbers()
    assert nums.e == 65537
    assert pk.key_size == 512


def test_encrypt_is_base64_of_one_rsa_block():
    pk = load_public_key(KEY)
    ct = encrypt_login_field("03", pk)
    raw = base64.b64decode(ct)
    assert len(raw) == pk.key_size // 8    # 64 bytes for RSA-512


def test_round_trip_under_a_local_keypair_recovers_plaintext():
    # We hold no private half of AKD's key, so prove correctness against a
    # locally generated keypair: PKCS#1 v1.5 encrypt then decrypt == input.
    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    ct = encrypt_login_field("hunter2", priv.public_key())
    pt = priv.decrypt(base64.b64decode(ct), padding.PKCS1v15())
    assert pt == b"hunter2"


def test_padding_randomises_so_two_encryptions_differ():
    pk = load_public_key(KEY)
    assert encrypt_login_field("03", pk) != encrypt_login_field("03", pk)


def test_round_trip_various_plaintext_lengths_local_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pub = priv.public_key()
    for plaintext in ("u", "testuser", "testpass", "03", "A" * 40):
        ct = encrypt_login_field(plaintext, pub)
        raw = base64.b64decode(ct)
        assert len(raw) == pub.key_size // 8
        pt = priv.decrypt(raw, padding.PKCS1v15())
        assert pt == plaintext.encode("utf-8")


def test_load_public_key_still_works_on_the_real_key_after_hardening():
    # Pins the happy path so the failure-mode hardening below can't have
    # silently broken it.
    pk = load_public_key(KEY)
    nums = pk.public_numbers()
    assert nums.e == 65537
    assert pk.key_size == 512


def test_unrecognized_field_typecode_raises(tmp_path):
    # Corrupt the "signum" field's typecode byte (offset 99 in the real
    # key: `I 00 06 signum`, typecode 'I' = 0x49) to something the parser
    # doesn't know how to skip. Without an explicit else-raise, the
    # if/elif chain over typecodes silently fails to advance the stream
    # position for this field, desynchronising every read after it - a
    # future key rotation could hit this and get a wrong (n, e) with no
    # clear signal. The parser must fail loudly instead.
    raw = bytearray(KEY.read_bytes())
    assert raw[99] == ord("I"), "test fixture assumption: signum field typecode at offset 99"
    raw[99] = ord("Z")
    corrupt_key = tmp_path / "corrupt_typecode.key"
    corrupt_key.write_bytes(bytes(raw))

    with pytest.raises(ValueError, match="unexpected field typecode"):
        load_public_key(corrupt_key)


def test_truncated_key_raises(tmp_path):
    # A key file that's missing its tail (a truncated download, a different
    # key format, etc.) must not be silently accepted with wrong/partial
    # data read as the modulus or exponent.
    raw = KEY.read_bytes()
    truncated_key = tmp_path / "truncated.key"
    truncated_key.write_bytes(raw[:-5])

    with pytest.raises(ValueError):
        load_public_key(truncated_key)
