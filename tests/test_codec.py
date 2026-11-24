from pathlib import Path

from akd.protocol.codec import xor

FIX = Path(__file__).resolve().parent / "fixtures" / "trade_c2s.bin"


def test_xor_is_the_documented_involution():
    assert xor(b"MSG-CLIENT") == b"PNZ0^QTXSI"  # protocol map §3
    assert xor(xor(b"MSG-CLIENT")) == b"MSG-CLIENT"  # involutive
    assert xor(b"|") == b"a"
    assert xor(bytes(range(256))) == bytes(b ^ 0x1D for b in range(256))


def test_the_real_recorded_frames_decode():
    frames = xor(FIX.read_bytes()).decode("latin-1").split("*")
    first = frames[0].split("|")
    assert first[:3] == ["MSG-CLIENT", "AUTHENTICATE_PARTIAL", "ACCT-TEST"] and first[4] == "1~2~3~4"
    assert "MSG-CLIENT|HI_FROM_CLIENT||" in frames          # an empty payload field survives as ""
