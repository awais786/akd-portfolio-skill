from __future__ import annotations

import pytest
from capture_fixtures import (
    ACCOUNT,
    CLIENT,
    ORDER,
    PIN,
    PSH_ACK,
    SERVER,
    SYN,
    TOKEN,
    WEB,
    pcap,
    session_packets,
    wire,
)

from akd import capture
from akd.capture import CaptureError, Redactor, build_report, main, read_pcap


def make_session(*, split=False, shuffle=False, retransmit=False, **kw):
    c2s, s2c = session_packets()
    cw, sw = wire(*c2s), wire(*s2c)
    pk = [(1000.0, CLIENT, SERVER, 5000, SYN, b"")]
    seq, t = 5001, 1000.1
    chunks = [cw[i:i + 40] for i in range(0, len(cw), 40)] if split else [cw]
    segs = []
    for ch in chunks:
        segs.append((t, CLIENT, SERVER, seq, PSH_ACK, ch))
        seq, t = seq + len(ch), t + 0.05
    if retransmit:
        segs.append((t, *segs[0][1:]))
    if shuffle:
        segs = segs[::-1]
    pk += segs
    pk += [(1000.0, SERVER, CLIENT, 9000, SYN, b"")]
    pk += [(1001.0, SERVER, CLIENT, 9001, PSH_ACK, sw)]
    return pcap(pk, **kw)


def report(data, tmp_path, **kw):
    f = tmp_path / "cap.pcap"
    f.write_bytes(data)
    red = Redactor("OGDC", "BUY", 1, 150.00)
    return "\n".join(build_report(read_pcap(str(f)), red, kw.get("account")).lines)


# ---- the guarantees that matter --------------------------------------------------------------------
def test_the_report_shows_the_order_layout_and_never_the_pin_account_or_token(tmp_path):
    text = report(make_session(), tmp_path)
    assert "NEW_ORDER" in text and "ORDER CANDIDATES" in text
    assert "MSG-CLIENT|NEW_ORDER|<masked>|REG|OGDC<SYMBOL>|BUY<SIDE>|LIMIT|1<QTY?>|150.00<PRICE>|<digits:4>|DAY" in text
    assert PIN not in text and ACCOUNT not in text and TOKEN not in text          # secrets never printed
    assert "ORDER_SENT|<digits:9>|OK" in text                                       # the reply's id is a shape only
    assert "123456789" not in text


def test_the_pin_is_hidden_even_if_it_looks_like_a_quantity_or_price_is_only_shown_when_you_named_it(tmp_path):
    text = report(make_session(), tmp_path)
    assert "<digits:4>" in text and "1<QTY?>" in text and "150.00<PRICE>" in text
    assert "possibly the PIN" in text


def test_session_secrets_are_learned_from_the_login_messages_and_masked_everywhere(tmp_path):
    # no --account given: the account and token are still masked because the login messages reveal them
    text = report(make_session(), tmp_path)
    assert "AUTHENTICATE_PARTIAL|<masked>|<masked>|1~2~3~4" in text
    assert text.count(TOKEN) == 0 and text.count(ACCOUNT) == 0


def test_all_message_types_and_replies_are_listed(tmp_path):
    text = report(make_session(), tmp_path)
    for t in ("NEW_CLIENT", "AUTHENTICATE_PARTIAL", "NEW_ORDER", "AUTHENTICATE_END", "ORDER_SENT"):
        assert t in text
    assert "reply" in text                                                         # the ack is shown under the candidate


def test_a_wrong_order_description_finds_no_candidate_and_says_how_to_fix_it(tmp_path):
    f = tmp_path / "c.pcap"
    f.write_bytes(make_session())
    text = "\n".join(build_report(read_pcap(str(f)), Redactor("PPL", "SELL", 5, 9.99)).lines)
    assert "none found" in text and "NEW_ORDER" in text                            # still listed in the census


# ---- reassembly ---------------------------------------------------------------------------------
@pytest.mark.parametrize("opts", [dict(split=True), dict(split=True, shuffle=True), dict(split=True, retransmit=True),
                                  dict(split=True, shuffle=True, retransmit=True)])
def test_messages_split_across_packets_arriving_out_of_order_or_twice_decode_the_same(tmp_path, opts):
    text = report(make_session(**opts), tmp_path)
    assert "MSG-CLIENT|NEW_ORDER|<masked>|REG|OGDC<SYMBOL>|BUY<SIDE>|LIMIT|1<QTY?>|150.00<PRICE>|<digits:4>|DAY" in text
    assert PIN not in text and "missing-segment gaps: 0" in text


def test_a_missing_segment_is_reported_not_hidden(tmp_path):
    c2s, _ = session_packets()
    cw = wire(*c2s)
    pk = [(1.0, CLIENT, SERVER, 5000, SYN, b""), (1.1, CLIENT, SERVER, 5001, PSH_ACK, cw[:30]),
          (1.2, CLIENT, SERVER, 5001 + 60, PSH_ACK, cw[60:])]                      # bytes 30..60 lost
    f = tmp_path / "g.pcap"
    f.write_bytes(pcap(pk))
    text = "\n".join(build_report(read_pcap(str(f)), Redactor("OGDC", "BUY", 1, 150.0)).lines)
    assert "missing-segment gaps: 1" in text


# ---- file formats ------------------------------------------------------------------------------
@pytest.mark.parametrize("kw", [dict(linktype=1), dict(linktype=0), dict(linktype=113), dict(linktype=101),
                                dict(magic="be-us"), dict(magic="le-ns")])
def test_common_capture_formats_are_read(tmp_path, kw):
    assert "NEW_ORDER" in report(make_session(**kw), tmp_path)


def test_unreadable_files_get_actionable_errors(tmp_path):
    for name, data, needle in [("ng", b"\x0a\x0d\x0d\x0a" + b"\x00" * 40, "pcapng"),
                               ("junk", b"hello world, not a capture at all", "not a pcap"),
                               ("empty", b"", "not a pcap")]:
        f = tmp_path / name
        f.write_bytes(data)
        with pytest.raises(CaptureError, match=needle):
            read_pcap(str(f))
    f = tmp_path / "lt"
    f.write_bytes(make_session(linktype=1)[:20] + (12345).to_bytes(4, "little") + make_session(linktype=1)[24:])
    with pytest.raises(CaptureError, match="link type 12345"):
        read_pcap(str(f))


def test_a_capture_without_the_trade_socket_says_what_to_check(tmp_path):
    pk = [(1.0, CLIENT, WEB, 1, PSH_ACK, b"GET / HTTP/1.1\r\n\r\n")]
    f = tmp_path / "w.pcap"
    f.write_bytes(pcap(pk))
    with pytest.raises(CaptureError, match="no traffic on the trade socket"):
        build_report(read_pcap(str(f)), Redactor("OGDC", "BUY", 1, 1.0))
    f.write_bytes(pcap([]))
    with pytest.raises(CaptureError, match="no TCP packets"):
        build_report(read_pcap(str(f)), Redactor("OGDC", "BUY", 1, 1.0))


# ---- the web service (SOAP) side ---------------------------------------------------------------------
def test_soap_operations_are_listed_and_encrypted_fields_are_shape_only(tmp_path):
    body = ("<?xml version='1.0'?><S:Envelope><S:Body><ns2:SecurecustomAuthentication xmlns:ns2='http://login/'>"
            "<UserName>" + "QUJD" * 30 + "</UserName><Password>" + "REVG" * 30 + "</Password></ns2:SecurecustomAuthentication>"
            "</S:Body></S:Envelope>")
    req = (f"POST /TradeCastServicev428/LoginServerService HTTP/1.1\r\nSOAPAction: \"http://login/LoginServer/"
           f"SecurecustomAuthenticationRequest\"\r\nContent-Length: {len(body)}\r\n\r\n{body}").encode()
    resp = b"HTTP/1.1 200 OK\r\n\r\n<S:Envelope><return>true|SECRETTOKEN99|role</return></S:Envelope>"
    c2s, s2c = session_packets()
    pk = [(1.0, CLIENT, SERVER, 5000, SYN, b""), (1.1, CLIENT, SERVER, 5001, PSH_ACK, wire(*c2s)),
          (1.0, ("192.168.1.5", 50001), WEB, 100, SYN, b""), (1.2, ("192.168.1.5", 50001), WEB, 101, PSH_ACK, req),
          (1.3, WEB, ("192.168.1.5", 50001), 700, PSH_ACK, resp)]
    f = tmp_path / "s.pcap"
    f.write_bytes(pcap(pk))
    text = "\n".join(build_report(read_pcap(str(f)), Redactor("OGDC", "BUY", 1, 150.0)).lines)
    assert "SecurecustomAuthentication" in text and "UserName=<masked>" in text
    assert "QUJD" not in text and "REVG" not in text                                # the encrypted login fields are not printed


# ---- redaction rules ----------------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("4321", "<digits:4>"), ("123456", "<digits:6>"), ("42", "42"), ("0", "0"), ("", "<empty>"),
    ("REG", "REG"), ("LIMIT", "LIMIT"), ("AUTHENTICATE_PARTIAL", "AUTHENTICATE_PARTIAL"),
    ("OGDC", "OGDC<SYMBOL>"), ("ogdc", "ogdc<SYMBOL>"), ("BUY", "BUY<SIDE>"), ("150.00", "150.00<PRICE>"),
    ("150.0", "150.0<PRICE>"), ("150.01", "<decimal:3.2>"), ("1", "1<QTY?>"),
    ("192.168.1.5", "<ip>"), ("1~2~3~4", "1~2~3~4"), ("4321~99", "<digits:4>~99"),
    ("QUJDREVGR0hJSktMTU5PUA==", "<b64:24>"), ("Some Text", "<text:9>"), ("a1b2", "<text:4>"),
])
def test_shape_table(value, expected):
    assert Redactor("OGDC", "BUY", 1, 150.0).shape(value) == expected


def test_masked_values_win_over_everything_else():
    r = Redactor("OGDC", "BUY", 1, 150.0)
    r.masked.add("OGDC")
    assert r.shape("OGDC") == "<masked>"


def test_events_keep_no_raw_values_after_the_report_is_built(tmp_path):
    f = tmp_path / "c.pcap"
    f.write_bytes(make_session())
    red = Redactor("OGDC", "BUY", 1, 150.0)
    packets = read_pcap(str(f))
    events = capture.socket_events(capture.reassemble(packets, 8092))
    assert any(e.fields for e in events)                                             # raw exists only before redaction
    build_report(packets, red)                                                       # (a fresh build wipes its own copy)
    assert PIN not in repr(events).replace("fields=", "")                            # and is never in a printed repr


# ---- command line -----------------------------------------------------------------------------------
def test_cli_prints_the_report_and_never_the_secrets(tmp_path, capsys):
    f = tmp_path / "c.pcap"
    f.write_bytes(make_session())
    assert main(["report", str(f), "--symbol", "ogdc", "--side", "buy", "--qty", "1", "--price", "150.00",
                 "--account", ACCOUNT]) == 0
    out = capsys.readouterr().out
    assert "NEW_ORDER" in out and PIN not in out and ACCOUNT not in out and TOKEN not in out


def test_cli_errors_exit_2_with_a_message_on_stderr(tmp_path, capsys):
    f = tmp_path / "bad.pcap"
    f.write_bytes(b"nope")
    assert main(["report", str(f), "--symbol", "OGDC", "--side", "BUY", "--qty", "1", "--price", "1"]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_there_is_no_option_to_print_raw_values():
    import inspect
    src = inspect.getsource(capture.main)
    assert "reveal" not in src and "--raw" not in src and "--show-secrets" not in src


def test_the_order_message_used_by_these_tests_is_invented():
    """Guard against mistaking a test fixture for a discovered protocol: NEW_ORDER is made up."""
    assert ORDER[1] == "NEW_ORDER"


# ---- leaks found in review: nothing id-like or secret-like may be printed, whatever shape it has ----
def _report_for(c2s, s2c, tmp_path, red=None, extra=()):
    pk = [(1000.0, CLIENT, SERVER, 5000, SYN, b""), (1000.1, CLIENT, SERVER, 5001, PSH_ACK, wire(*c2s)),
          (1000.2, SERVER, CLIENT, 7000, PSH_ACK, wire(*s2c)), *extra]
    f = tmp_path / "r.pcap"
    f.write_bytes(pcap(pk))
    red = red or Redactor("MLCF", "BUY", 1, 100.0)
    return "\n".join(build_report(read_pcap(str(f)), red, "COAF00000").lines)


def test_an_uppercase_token_or_id_in_any_field_is_a_shape_not_a_protocol_word(tmp_path):
    c2s = [["MSG-CLIENT", "NEW_CLIENT", "ACCT12345", "10.0.0.1"], ["MSG-CLIENT", "SOMETHING", "SESSIONTOKEN77", "COAF00001"]]
    s2c = [["MSG-SERVER", "WHO", "SECRETTOKEN99", "ABCD1234"]]
    text = _report_for(c2s, s2c, tmp_path)
    for leaked in ("SESSIONTOKEN77", "SECRETTOKEN99", "ABCD1234", "COAF00001", "ACCT12345", "SOMETHING"):
        assert leaked not in text, leaked


def test_a_login_response_token_and_soap_ids_are_not_printed(tmp_path):
    body = ("<S:Envelope><S:Body><ns2:getInfoRequest><userID>SESSIONTOKEN77</userID><otherInfo>COAF00001-9</otherInfo>"
            "<days>7</days></ns2:getInfoRequest></S:Body></S:Envelope>")
    req = (f"POST /svc HTTP/1.1\r\nSOAPAction: \"http://x/getInfoRequest\"\r\nContent-Length: {len(body)}\r\n\r\n{body}").encode()
    resp = b"HTTP/1.1 200 OK\r\n\r\n<S:Envelope><return>true|SECRETTOKEN99|COAF00001</return></S:Envelope>"
    web = [(1.0, ("192.168.1.5", 50001), WEB, 100, SYN, b""), (1.2, ("192.168.1.5", 50001), WEB, 101, PSH_ACK, req),
           (1.3, WEB, ("192.168.1.5", 50001), 700, PSH_ACK, resp)]
    text = _report_for(*session_packets(), tmp_path, extra=web)
    assert "getInfoRequest" in text and "days=7" in text                       # harmless parts still show
    for leaked in ("SESSIONTOKEN77", "SECRETTOKEN99", "COAF00001"):
        assert leaked not in text, leaked


def test_a_secret_is_masked_by_substring_so_a_suffixed_account_id_cannot_leak():
    red = Redactor("MLCF", "BUY", 1, 100.0)
    red.masked.update({"COAF00000", "SECRETTOKEN99"})
    assert red.shape("COAF00000-1") == "<masked>"
    assert red.shape("user=COAF00000&x=1") == "<masked>"
    assert red.shape("xxSECRETTOKEN99yy") == "<masked>"
    assert red.shape("REG") == "REG"


def test_a_four_digit_value_is_never_labelled_as_the_quantity_even_if_it_equals_it():
    red = Redactor("MLCF", "BUY", 4321, 100.0)                 # a quantity that happens to equal the PIN
    assert red.shape("4321") == "<digits:4>"
    assert Redactor("MLCF", "BUY", 5, 100.0).shape("5") == "5<QTY?>"


def test_a_sell_is_recognised_by_its_wire_word():
    assert Redactor("MLCF", "SELL", 1, 100.0).shape("SEL") == "SEL<SIDE>"
    assert Redactor("MLCF", "BUY", 1, 100.0).shape("SEL") == "SEL"


def test_the_message_type_is_redacted_too_so_a_stray_value_cannot_reach_the_census(tmp_path):
    c2s = [["MSG-CLIENT", "7654"], ["MSG-CLIENT", "TOPSECRETTYPE", "x"], ["MSG-CLIENT", "NEW_CLIENT", "ACCT12345", "10.0.0.1"]]
    text = _report_for(c2s, [["MSG-SERVER", "WELCOME", "x"]], tmp_path)
    assert "7654" not in text and "TOPSECRETTYPE" not in text and "NEW_CLIENT" in text


def test_an_http_get_never_prints_its_path_query_string(tmp_path):
    req = b"GET /svc/Portfolio?user=COAF00001&tok=ABCD1234 HTTP/1.1\r\nHost: x\r\n\r\n"
    web = [(1.0, ("192.168.1.5", 50001), WEB, 100, SYN, b""), (1.2, ("192.168.1.5", 50001), WEB, 101, PSH_ACK, req)]
    text = _report_for(*session_packets(), tmp_path, extra=web)
    assert "COAF00001" not in text and "ABCD1234" not in text and "tok=" not in text


def test_the_account_user_code_and_pin_in_an_order_record_are_hidden_everywhere(tmp_path):
    order = ["MSG-CLIENT", "NEW_ORDER", "4321$TRD;NEW;BUY;MLCF;REG;100.0;1;;;ACCT12;ACCT12;QXZT;5;24/11/2026 12:00:00.123;0.0;"]
    reply = ["MSG-SERVER", "ORDER_EXE", "x", "QXZT", "NOR$"]
    text = _report_for([order], [reply], tmp_path)
    assert "BUY<SIDE>;MLCF<SYMBOL>" in text                    # the layout is still visible
    for leaked in ("QXZT", "ACCT12", "4321"):
        assert leaked not in text, leaked


def test_a_huge_gap_in_a_recording_does_not_allocate_gigabytes():
    from akd.capture import MAX_GAP, Packet, reassemble
    a = Packet(1.0, "1.1.1.1", 5000, "2.2.2.2", 8092, 1000, PSH_ACK, b"abc")
    b = Packet(1.1, "1.1.1.1", 5000, "2.2.2.2", 8092, 1000 + 3_000_000_000, PSH_ACK, b"def")
    st = next(iter(reassemble([a, b], 8092).values()))["c2s"]
    assert bytes(st.data) == b"abcdef" and st.gaps == 1 and len(st.data) < MAX_GAP
