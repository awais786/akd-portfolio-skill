from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from akd.protocol.decode import (
    decode_collateral,
    decode_order_history,
    extract_return,
)

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)


def test_decodes_positions_from_a_real_collateral_response():
    text = (FIXTURES / "collateral_sample.txt").read_text()
    positions = decode_collateral(text, as_of=AS_OF)
    assert positions, "no positions decoded"
    first = positions[0]
    assert first.symbol == first.symbol.upper()
    assert first.quantity > 0
    assert first.as_of == AS_OF


def test_every_position_carries_the_capture_time():
    text = (FIXTURES / "collateral_sample.txt").read_text()
    assert all(p.as_of == AS_OF for p in decode_collateral(text, as_of=AS_OF))


def test_empty_body_decodes_to_an_empty_list_not_an_error():
    assert decode_collateral("", as_of=AS_OF) == []


def test_malformed_record_is_skipped_not_fatal():
    """One bad row must not lose the whole book."""
    text = "OGDC;100;;;150.5;;\nGARBAGE_ROW_WITHOUT_FIELDS\nHBL;50;;;95.0;;\n"
    out = decode_collateral(text, as_of=AS_OF)
    assert {p.symbol for p in out} == {"OGDC", "HBL"}


def test_a_garbled_or_missing_cost_field_survives_with_avg_cost_none():
    """DELIBERATE divergence from the original ingest script's
    parse_collateral: that parser decodes quantity and cost in one
    try/except, so a garbled cost column
    drops the whole row. Here the position still counts - the user still
    owns those shares even when the broker's cost column is unreadable."""
    header = (
        "Symbol;Quantity;TotalQty;Avg. BUY RATE;Sold Quantity;Avg. SELL RATE;"
        "MTM;MTM Amount;Haircut %;Marginized Value;After Haircut;PENDING SELL;"
        "SETTLED P/L;UNSETTLED P/L"
    )
    rows = [
        "AAAA;100;100;120.5;0;0;0;0;0;0;0;0;0;0",   # normal
        "BBBB;50;50;abc;0;0;0;0;0;0;0;0;0;0",         # unparsable cost
        "CCCC;75;75",                                  # cost field missing entirely
        "DDDD;40;40;99.9;0;0;0;0;0;0;0;0;0;0",        # normal, unaffected
    ]
    text = header + "|" + "|".join(rows)
    out = decode_collateral(text, as_of=AS_OF)
    by_symbol = {p.symbol: p for p in out}

    assert set(by_symbol) == {"AAAA", "BBBB", "CCCC", "DDDD"}
    assert by_symbol["AAAA"].avg_cost == 120.5
    assert by_symbol["BBBB"].avg_cost is None
    assert by_symbol["BBBB"].quantity == 50
    assert by_symbol["CCCC"].avg_cost is None
    assert by_symbol["CCCC"].quantity == 75
    assert by_symbol["DDDD"].avg_cost == 99.9


def test_header_missing_the_cost_column_raises_instead_of_guessing():
    """A header IS present (it names Symbol/Quantity) but doesn't carry
    "Avg. BUY RATE" - e.g. AKD renamed or reordered the column. Falling back
    to a hardcoded index here would silently attach the wrong number as the
    user's cost basis, which is worse than no cost basis at all."""
    text = "Symbol;Quantity;TotalQty;Sold Quantity|OGDC;100;100;0"
    with pytest.raises(ValueError, match=r"Avg\. BUY RATE"):
        decode_collateral(text, as_of=AS_OF)


def test_header_missing_the_quantity_column_raises_instead_of_guessing():
    text = "Symbol;TotalQty;Avg. BUY RATE|OGDC;100;150.0"
    with pytest.raises(ValueError, match="Quantity"):
        decode_collateral(text, as_of=AS_OF)


def test_headerless_input_still_decodes_on_documented_default_positions():
    """No row here reads as a header at all - this is the path the brief's
    own malformed-row fixture exercises, and it must keep working on the
    documented default column positions rather than raise."""
    text = "OGDC;100;;;150.5;;\nHBL;50;;;95.0;;\n"
    out = decode_collateral(text, as_of=AS_OF)
    assert {p.symbol for p in out} == {"OGDC", "HBL"}


def test_extract_return_pulls_the_soap_body():
    xml = "<soap:Envelope><return>PAYLOAD</return></soap:Envelope>"
    assert extract_return(xml) == "PAYLOAD"


def test_extract_return_handles_a_missing_return_element():
    assert extract_return("<soap:Envelope/>") is None


# --- decode_order_history ---------------------------------------------------


def test_decodes_dated_fills_from_plaintext_order_history():
    text = (
        "OGDC;100;150.50;15050.00;BUY;T+1REG;Jun 15, 2026;Jun 16, 2026;"
        "|HBL;50;95.00;4750.00;SELL;T+1REG;Jun 16, 2026;Jun 17, 2026;"
    )
    out = decode_order_history(text, as_of=AS_OF)
    assert {o.symbol for o in out} == {"OGDC", "HBL"}
    ogdc = next(o for o in out if o.symbol == "OGDC")
    assert ogdc.side == "BUY"
    assert ogdc.quantity == 100
    assert ogdc.price == 150.50
    assert ogdc.status == "FILLED"
    assert ogdc.placed_at.year == 2026 and ogdc.placed_at.month == 6 and ogdc.placed_at.day == 15
    assert ogdc.order_id  # synthesised, but always present and non-empty


def test_order_history_unwraps_base64_gzip_payloads():
    import base64
    import gzip

    plain = "MUGHAL;532;86.65;46179.99;BUY;T+1REG;Jun 15, 2026;Jun 16, 2026;"
    wrapped = base64.b64encode(gzip.compress(plain.encode())).decode()
    out = decode_order_history(wrapped, as_of=AS_OF)
    assert [o.symbol for o in out] == ["MUGHAL"]


def test_order_history_malformed_record_is_skipped_not_fatal():
    text = "OGDC;100;150.5;15050;BUY;T+1REG;Jun 15, 2026;\nNOT;ENOUGH;FIELDS\n"
    out = decode_order_history(text, as_of=AS_OF)
    assert {o.symbol for o in out} == {"OGDC"}


def test_order_history_empty_body_decodes_to_an_empty_list():
    assert decode_order_history("", as_of=AS_OF) == []


def test_order_history_ids_are_stable_under_record_reordering():
    """order_id must come from a record's own content, not the position it
    happens to occupy in the input - Task 12 content-hashes these payloads,
    and a position-derived id would make reordered-but-identical input look
    like a change (or hide a real one). Two same-day OGDC fills with
    different quantity/price are the case that would previously have swapped
    ids under reversal."""
    records = [
        "OGDC;100;150.00;15000.00;BUY;T+1REG;Jun 15, 2026;Jun 16, 2026;",
        "OGDC;50;151.00;7550.00;BUY;T+1REG;Jun 15, 2026;Jun 16, 2026;",
        "HBL;30;95.00;2850.00;SELL;T+1REG;Jun 15, 2026;Jun 16, 2026;",
    ]
    forward = decode_order_history("|".join(records), as_of=AS_OF)
    backward = decode_order_history("|".join(reversed(records)), as_of=AS_OF)

    def as_map(orders):
        return {o.order_id: (o.symbol, o.side, o.quantity, o.price) for o in orders}

    assert len(forward) == 3
    assert as_map(forward) == as_map(backward)
