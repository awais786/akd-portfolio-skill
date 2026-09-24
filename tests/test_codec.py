from pathlib import Path

import pytest

from akd.protocol.codec import encode_message, iter_messages, xor

FIX = Path(__file__).resolve().parent / "fixtures" / "trade_c2s.bin"


def test_xor_is_the_documented_involution():
    assert xor(b"MSG-CLIENT") == b"PNZ0^QTXSI"  # protocol map §3
    assert xor(xor(b"MSG-CLIENT")) == b"MSG-CLIENT"  # involutive
    assert xor(b"|") == b"a"


def test_real_authenticate_partial_frame_decodes():
    raw = FIX.read_bytes()
    messages, rest = iter_messages(raw)
    first = messages[0]
    assert first[0] == "MSG-CLIENT"
    assert first[1] == "AUTHENTICATE_PARTIAL"
    assert first[2] == "ACCT-TEST"
    assert first[4] == "1~2~3~4"  # the ~-delimited positional tail, intact
    assert rest == b""  # fixture is a whole number of complete frames
    # The fixture also carries HI_FROM_CLIENT frames whose one payload field
    # is empty (Review Focus 4) - the empty field must survive as "", neither
    # dropped nor merged with a neighbour.
    assert ["MSG-CLIENT", "HI_FROM_CLIENT", ""] in messages


def test_encode_round_trips_a_field_value_containing_delimiters():
    # Review Focus 2: a field carrying a literal '|', '~' and '*' must survive
    # a round trip, or encode must refuse it loudly rather than silently
    # mis-split the frame on decode.
    #
    # '~' is the in-field sub-list delimiter (e.g. the "1~2~3~4" positional
    # tail) - it never breaks frame/field splitting, so a field containing it
    # round-trips unchanged.
    wire = encode_message(["POSITIONS", "AB~CD~EF"])
    msgs, rest = iter_messages(wire)
    assert msgs == [["MSG-CLIENT", "POSITIONS", "AB~CD~EF"]]
    assert rest == b""

    # AKD's MyTcp.FormatMessage has no in-band escape for the framing
    # delimiters themselves ('|' the field separator, '*' the terminator):
    # a raw one inside a field value would mis-split or truncate the frame on
    # decode, so encode_message refuses to build the frame at all.
    with pytest.raises(ValueError):
        encode_message(["ORDER", "AB|CD"])
    with pytest.raises(ValueError):
        encode_message(["ORDER", "AB*CD"])

    # A benign field (no framing delimiters) round-trips through
    # encode -> iter_messages unchanged.
    wire = encode_message(["HI_FROM_CLIENT", "ACCT-TEST"])
    msgs, rest = iter_messages(wire)
    assert msgs == [["MSG-CLIENT", "HI_FROM_CLIENT", "ACCT-TEST"]]
    assert rest == b""


def test_iter_messages_returns_trailing_incomplete_bytes():
    whole = encode_message(["A", "B"]) + encode_message(["C"])
    msgs, rest = iter_messages(whole[:-3])  # chop the last frame's tail
    assert msgs == [["MSG-CLIENT", "A", "B"]]
    assert rest != b""  # the C-frame's head is carried across the read boundary


def test_iter_messages_handles_multiple_frames_back_to_back():
    whole = encode_message(["A", "1"]) + encode_message(["B", "2"]) + encode_message(["C", "3"])
    msgs, rest = iter_messages(whole)
    assert msgs == [
        ["MSG-CLIENT", "A", "1"],
        ["MSG-CLIENT", "B", "2"],
        ["MSG-CLIENT", "C", "3"],
    ]
    assert rest == b""


# --- Degenerate inputs (Review Focus 4) -------------------------------------


def test_iter_messages_of_empty_buffer_returns_no_messages_and_no_remainder():
    messages, rest = iter_messages(b"")
    assert messages == []
    assert rest == b""


def test_iter_messages_of_a_lone_terminator_yields_no_messages_not_one_empty_message():
    # Contract: an empty frame (nothing before the '*') is not a message -
    # a lone terminator with no fields ahead of it decodes to zero messages,
    # not a one-element message list.
    messages, rest = iter_messages(xor(b"*"))
    assert messages == []
    assert rest == b""


def test_empty_field_survives_encode_decode_round_trip_not_dropped_or_merged():
    wire = encode_message(["HI_FROM_CLIENT", ""])
    messages, rest = iter_messages(wire)
    assert messages == [["MSG-CLIENT", "HI_FROM_CLIENT", ""]]
    assert rest == b""


def test_encode_message_with_no_fields_is_a_valid_bare_prefix_frame():
    # Deliberate contract: an empty field list is not an error - it encodes
    # to a bare 'MSG-CLIENT|*' frame and decodes back to just the prefix,
    # with no fields.
    wire = encode_message([])
    messages, rest = iter_messages(wire)
    assert messages == [["MSG-CLIENT"]]
    assert rest == b""
