from __future__ import annotations

import base64
import random
from pathlib import Path

from akd.config import SERVER_CODE
from akd.protocol import soap
from akd.protocol.crypto import load_public_key

FIX = Path(__file__).resolve().parent / "fixtures"
KEY = Path(__file__).resolve().parents[1] / "src" / "akd" / "public.key"


def test_getcollateral_builder_matches_captured_envelope_shape():
    body = soap.build_getcollateral("ACCT-TEST", "ACCT-TEST")
    assert '<ns2:GetCollateral xmlns:ns2="http://login/">' in body
    assert "<UserID>ACCT-TEST</UserID>" in body
    assert "<Account>ACCT-TEST</Account>" in body
    assert body.startswith("<?xml")


def test_getorderhistory_builder_matches_captured_envelope_shape():
    body = soap.build_getorderhistory("ACCT-TEST", "ACCT-TEST")
    assert '<ns2:GetOrderHistory xmlns:ns2="http://login/">' in body
    assert "<UserID>ACCT-TEST</UserID>" in body
    assert "<Account>ACCT-TEST</Account>" in body


def test_getaccountstatement_builder_matches_captured_envelope_shape():
    body = soap.build_getaccountstatement("ACCT-TEST", "ACCT-TEST")
    assert '<ns2:GetAccountStatement xmlns:ns2="http://login/">' in body


def test_secure_auth_builder_has_four_rsa_base64_fields():
    pk = load_public_key(KEY)
    body = soap.build_secure_auth("user", "pass", "", pk, server_code=SERVER_CODE)
    for tag in ("UserName", "Password", "PasswordPosition", "ServerCode"):
        assert f"<{tag}>" in body
    # ServerCode field is RSA-encrypted "03", not the literal "03"
    assert "<ServerCode>03</ServerCode>" not in body


def test_secure_auth_fields_are_valid_base64():
    import re

    pk = load_public_key(KEY)
    body = soap.build_secure_auth("user", "pass", "", pk, server_code=SERVER_CODE)
    for tag in ("UserName", "Password", "PasswordPosition", "ServerCode"):
        m = re.search(f"<{tag}>(.*?)</{tag}>", body)
        assert m, f"missing {tag}"
        base64.b64decode(m.group(1))  # raises if not valid base64


def test_parse_auth_response_reads_the_real_capture():
    xml = (FIX / "securecustom_resp.xml").read_text()
    out = soap.parse_auth_response(xml)
    assert out["valid"] is True
    assert out["token"] == "TOK-SESSION"
    assert out["role"] == "Trader"


def test_parse_auth_response_false_when_not_true():
    out = soap.parse_auth_response("<return>false|badlogin</return>")
    assert out["valid"] is False


def test_parse_auth_response_handles_missing_return_element():
    out = soap.parse_auth_response("<S:Envelope><S:Body/></S:Envelope>")
    assert out["valid"] is False
    assert out["token"] is None


# -- Partial-password (positional) login scheme ---------------------------
# Recovered from windows.LoginForm2.GetRandomNumber (nextInt(7)+1, distinct)
# and confirmed against a captured working SecurecustomAuthentication request:
# the client never sends the full password, but the characters at 4 distinct
# 1-based positions in [1,7], plus those positions pipe-joined.
def test_partial_password_fields_picks_chars_at_1based_positions():
    partial, position_field = soap.partial_password_fields("abcdefgh", [2, 4, 5, 7])
    assert partial == "bdeg"          # chars at positions 2,4,5,7 (1-based)
    assert position_field == "2|4|5|7"


def test_partial_password_fields_preserves_position_order():
    # Password chars and the position list must stay index-aligned.
    partial, position_field = soap.partial_password_fields("PASSWORD", [7, 1, 3])
    assert partial == "RPS"           # positions 7,1,3 -> R,P,S in that order
    assert position_field == "7|1|3"


def test_choose_login_positions_distinct_in_1_to_7():
    rng = random.Random(1234)
    pos = soap.choose_login_positions(10, rng=rng)
    assert len(pos) == 4
    assert len(set(pos)) == 4                 # distinct
    assert all(1 <= p <= 7 for p in pos)      # 1..7 span, never exceeding 7


def test_choose_login_positions_never_exceeds_short_password():
    rng = random.Random(1)
    pos = soap.choose_login_positions(5, rng=rng)   # password shorter than span
    assert all(1 <= p <= 5 for p in pos)             # never index past the password
    assert len(set(pos)) == len(pos)
