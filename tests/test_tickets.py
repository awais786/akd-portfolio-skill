"""Trade tickets: validation and pre-trade checks. Nothing here sends anything anywhere."""
from __future__ import annotations

import io
import json
import sys
from decimal import Decimal

import pytest

from akd import __main__ as entry
from akd.tickets import Facts, Limits, allowed, check_ticket, parse_ticket, run_ticket

LIM = Limits(max_value=100_000, price_band_pct=10, fee_buffer_pct=1)


def _ticket(side="BUY", qty=100, price="150.00", symbol="OGDC"):
    return parse_ticket(symbol, side, qty, price)


def _facts(**kw):
    base = {"free_cash": 1_000_000.0, "held_qty": 500, "ref_price": 150.0, "ref_source": "AKD mark"}
    return Facts(**{**base, **kw})


def _by_name(checks):
    return {c.name: c for c in checks}


# --- parsing ----------------------------------------------------------------------------------


def test_parse_accepts_a_clean_ticket_and_normalises_case():
    t = parse_ticket(" ogdc ", "buy", 100, "150.50")
    assert (t.symbol, t.side, t.quantity, t.limit_price) == ("OGDC", "BUY", 100, Decimal("150.50"))
    assert t.value == Decimal("15050.00")


@pytest.mark.parametrize(("args", "needle"), [
    (("", "BUY", 1, "10"), "symbol"),
    (("1OGDC", "BUY", 1, "10"), "symbol"),
    (("OGDC", "HOLD", 1, "10"), "BUY or SELL"),
    (("OGDC", "BUY", 0, "10"), "greater than 0"),
    (("OGDC", "BUY", -5, "10"), "greater than 0"),
    (("OGDC", "BUY", 1.5, "10"), "whole number"),
    (("OGDC", "BUY", True, "10"), "whole number"),
    (("OGDC", "BUY", 1, "abc"), "number"),
    (("OGDC", "BUY", 1, "0"), "greater than 0"),
    (("OGDC", "BUY", 1, "10.123"), "2 decimal"),
    (("OGDC", "BUY", 1, "nan"), "greater than 0"),
])
def test_parse_rejects_bad_input_with_a_human_message(args, needle):
    with pytest.raises(ValueError, match=needle):
        parse_ticket(*args)


def test_a_whole_number_float_quantity_is_accepted_but_a_fraction_is_not():
    assert parse_ticket("OGDC", "BUY", 100.0, "10").quantity == 100
    with pytest.raises(ValueError, match="whole number"):
        parse_ticket("OGDC", "BUY", 100.5, "10")


# --- checks -----------------------------------------------------------------------------------


def test_a_good_buy_passes_every_check():
    checks = check_ticket(_ticket(), _facts(), LIM)
    assert allowed(checks) and all(c.status == "pass" for c in checks)


def test_order_size_limit_is_inclusive():
    assert _by_name(check_ticket(_ticket(qty=1000, price="100.00"), _facts(ref_price=100.0), LIM))["Order size"].status == "pass"
    assert _by_name(check_ticket(_ticket(qty=1000, price="100.01"), _facts(ref_price=100.01), LIM))["Order size"].status == "fail"


def test_price_band_is_a_typo_guard_in_both_directions():
    for price in ("100.00", "200.00"):  # reference 150: far below and far above
        assert _by_name(check_ticket(_ticket(price=price, qty=10), _facts(), LIM))["Price sanity"].status == "fail"
    assert _by_name(check_ticket(_ticket(price="160.00", qty=10), _facts(), LIM))["Price sanity"].status == "pass"


def test_no_reference_price_only_warns_and_still_allows():
    checks = check_ticket(_ticket(), _facts(ref_price=None, ref_source=None), LIM)
    assert _by_name(checks)["Price sanity"].status == "warn" and allowed(checks)


def test_buy_needs_cash_including_the_fee_allowance():
    t = _ticket(qty=100, price="100.00")  # value 10,000 -> needs 10,100 with a 1% allowance
    assert _by_name(check_ticket(t, _facts(free_cash=10_100.0, ref_price=100.0), LIM))["Enough cash"].status == "pass"
    assert _by_name(check_ticket(t, _facts(free_cash=10_099.99, ref_price=100.0), LIM))["Enough cash"].status == "fail"


def test_unknown_cash_or_holdings_fail_closed():
    assert not allowed(check_ticket(_ticket("BUY"), _facts(free_cash=None), LIM))
    assert not allowed(check_ticket(_ticket("SELL"), _facts(held_qty=None), LIM))


def test_sell_cannot_exceed_holdings_no_short_selling():
    assert allowed(check_ticket(_ticket("SELL", qty=500), _facts(held_qty=500), LIM))
    blocked = check_ticket(_ticket("SELL", qty=501), _facts(held_qty=500), LIM)
    assert not allowed(blocked) and _by_name(blocked)["Shares held"].status == "fail"


def test_buy_does_not_check_holdings_and_sell_does_not_check_cash():
    assert "Shares held" not in _by_name(check_ticket(_ticket("BUY"), _facts(), LIM))
    assert "Enough cash" not in _by_name(check_ticket(_ticket("SELL"), _facts(), LIM))


def test_any_failed_check_means_not_allowed():
    assert not allowed(check_ticket(_ticket(qty=10_000), _facts(), LIM))  # over the size limit


# --- the command: `ticket buy ...` and `ticket sell ...` ---------------------------------------------


def _run(argv, env=None):
    out = io.StringIO()
    return run_ticket(argv, env or {}, out), json.loads(out.getvalue())


def _argv(side="buy", **kw):
    args = {"symbol": "OGDC", "qty": "100", "price": "150.00", "ref": "150", "ref-source": "AKD mark"}
    args["free-cash" if side == "buy" else "held"] = "1000000" if side == "buy" else "500"
    args.update(kw)
    return [side] + [x for k, v in args.items() if v is not None for x in (f"--{k}", v)]


def test_buy_allowed_exit_0_and_never_says_placed():
    code, doc = _run(_argv("buy"))
    assert code == 0 and doc["allowed"] is True and doc["placed"] is False
    assert doc["ticket"] == {"symbol": "OGDC", "side": "BUY", "quantity": 100, "limit_price": "150.00", "value": "15000.00"}


def test_sell_allowed_exit_0():
    code, doc = _run(_argv("sell"))
    assert code == 0 and doc["ticket"]["side"] == "SELL" and doc["placed"] is False


def test_buy_blocked_exit_1_when_cash_is_short():
    code, doc = _run(_argv("buy", **{"free-cash": "10"}))
    assert code == 1 and doc["allowed"] is False
    assert any(c["name"] == "Enough cash" and c["status"] == "fail" for c in doc["checks"])


def test_sell_blocked_exit_1_when_selling_more_than_held():
    code, doc = _run(_argv("sell", qty="501"))
    assert code == 1 and any(c["name"] == "Shares held" and c["status"] == "fail" for c in doc["checks"])


def test_each_side_needs_its_own_fact():
    code, doc = _run(_argv("buy", **{"free-cash": None}))
    assert code == 2 and "--free-cash" in doc["error"] and doc["placed"] is False
    code, doc = _run(_argv("sell", held=None))
    assert code == 2 and "--held" in doc["error"]


@pytest.mark.parametrize("argv", [
    [], ["--side", "BUY", "--symbol", "OGDC", "--qty", "1", "--price", "10"], ["hold", "--symbol", "OGDC"],
])
def test_a_missing_or_old_style_side_is_rejected_with_help(argv):
    code, doc = _run(argv)
    assert code == 2 and "ticket buy" in doc["error"] and "ticket sell" in doc["error"] and doc["placed"] is False


def test_bad_input_exit_2_with_a_message():
    code, doc = _run(_argv("buy", qty="0"))
    assert code == 2 and "greater than 0" in doc["error"] and doc["placed"] is False


def test_command_limits_come_from_the_environment():
    code, doc = _run(_argv("buy"), {"AKD_TICKET_MAX_VALUE": "1000"})
    assert code == 1 and any(c["name"] == "Order size" and c["status"] == "fail" for c in doc["checks"])


def test_command_echoes_the_account_label_and_it_never_changes_the_checks():
    code, doc = _run([*_argv("buy"), "--account", "COAF00000"])
    assert code == 0 and doc["account"] == "COAF00000" and doc["placed"] is False
    code2, doc2 = _run(_argv("buy"))
    assert doc2["account"] is None and doc2["checks"] == doc["checks"]


def test_main_dispatches_the_ticket_subcommand(monkeypatch):
    monkeypatch.setattr(entry, "run_ticket", lambda argv, env, out: 7)
    monkeypatch.setattr(sys, "argv", ["akd", "ticket", "buy"])
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 7


# ---- every limit is a variable: environment > limits file > default ----
from akd.tickets import DEFAULTS, _limits, read_limits_file  # noqa: E402


def test_limits_come_from_the_defaults_then_the_file_then_the_environment(tmp_path):
    f = tmp_path / "limits.env"
    f.write_text("AKD_TICKET_MAX_VALUE=250000\nAKD_TICKET_MAX_QTY=777\nAKD_PASSWORD=never\n")
    lim = _limits({"AKD_LIMITS_FILE": str(f)})
    assert (lim.max_value, lim.max_qty, lim.price_band_pct, lim.fee_buffer_pct) == (250_000, 777, 10, 1)
    lim = _limits({"AKD_LIMITS_FILE": str(f), "AKD_TICKET_MAX_QTY": "5", "AKD_TICKET_FEE_BUFFER_PCT": "0"})
    assert (lim.max_qty, lim.fee_buffer_pct) == (5, 0)             # the environment wins; 0 is fine for fees
    assert read_limits_file(f) == {"AKD_TICKET_MAX_VALUE": "250000", "AKD_TICKET_MAX_QTY": "777"}


@pytest.mark.parametrize("bad", ["inf", "nan", "-3", "0", "x", ""])
def test_a_broken_limit_falls_back_to_the_default(bad):
    lim = _limits({"AKD_LIMITS_FILE": "", "AKD_TICKET_MAX_VALUE": bad, "AKD_TICKET_MAX_QTY": bad,
                   "AKD_TICKET_PRICE_BAND_PCT": bad})
    assert (lim.max_value, lim.max_qty, lim.price_band_pct) == (100_000, 10_000, 10)


def test_the_quantity_limit_is_checked_and_named():
    checks = check_ticket(_ticket("BUY", 600, "1.00", "KTML"), _facts(ref_price=1.0), Limits(100_000, 10, 1, max_qty=500))
    q = next(c for c in checks if c.name == "Order quantity")
    assert q.status == "fail" and "AKD_TICKET_MAX_QTY" in q.message


def test_limits_default_values_match_the_documented_ones():
    assert DEFAULTS == {"AKD_TICKET_MAX_VALUE": "100000", "AKD_TICKET_MAX_QTY": "10000",
                        "AKD_TICKET_PRICE_BAND_PCT": "10", "AKD_TICKET_FEE_BUFFER_PCT": "1"}


# ---- odd numbers give exit 2 and JSON with "placed": false, never a traceback or the "blocked" exit 1 ----
@pytest.mark.parametrize("extra", [
    ["--qty", "10", "--price", "100", "--ref", "0"],            # ZeroDivisionError before
    ["--qty", "10", "--price", "100", "--ref", "-5"],
    ["--qty", "10", "--price", "100", "--ref", "inf"],
    ["--qty", "Infinity", "--price", "100"],                    # OverflowError before
    ["--qty", "sNaN", "--price", "100"],                        # InvalidOperation before
    ["--qty", "NaN", "--price", "100"],
    ["--qty", "10", "--price", "1e30"],                         # too many digits to round to cents
    ["--qty", "10", "--price", "100", "--free-cash", "-5"],
    ["--qt", "10", "--price", "100"],                           # an abbreviation is not accepted
])
def test_odd_numbers_are_bad_input_not_a_crash(extra):
    import io

    from akd.tickets import run_ticket
    out = io.StringIO()
    argv = ["buy", "--symbol", "OGDC", *([] if "--free-cash" in extra else ["--free-cash", "1000000"]), *extra]
    code = run_ticket(argv, {"AKD_LIMITS_FILE": ""}, out)
    doc = json.loads(out.getvalue())
    assert code == 2 and doc["placed"] is False and "error" in doc
