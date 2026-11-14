"""`python -m akd portfolio`: env in, one JSON document out, stable exit codes. Sync tests: the
command runs its own event loops (`asyncio.run`)."""
from __future__ import annotations

import io
import json
import logging
import sys

import httpx
import pytest
from portfolio_fakes import ACCOUNT, REJECTED_AUTH, SECRET, FakeHTTP

from akd import __main__ as entry
from akd.portfolio import PortfolioError, accounts_from_env, run_portfolio

ENV = {"AKD_USER_ID": ACCOUNT, "AKD_PASSWORD": SECRET}
SECOND, SECRET2 = "COAF00001", "other-pw-7"


def _run(env, http=None, http_for=None):
    out, err = io.StringIO(), io.StringIO()
    code = run_portfolio(env, out, err, http=http, http_for=http_for)
    return code, out.getvalue(), err.getvalue()


# --- reading accounts from the environment -------------------------------------------------------


def test_accounts_from_env_orders_slots_numerically_and_treats_unsuffixed_as_first():
    env = {"AKD_USER_ID_10": "J", "AKD_PASSWORD_10": "pj", "AKD_USER_ID_2": "B", "AKD_PASSWORD_2": "pb",
           "AKD_USER_ID": "A", "AKD_PASSWORD": "pa"}
    assert accounts_from_env(env) == [("A", "pa"), ("B", "pb"), ("J", "pj")]


def test_accounts_from_env_accepts_suffix_1_for_the_first_account():
    assert accounts_from_env({"AKD_USER_ID_1": "A", "AKD_PASSWORD_1": "pa"}) == [("A", "pa")]


def test_accounts_from_env_rejects_both_unsuffixed_and_suffix_1():
    with pytest.raises(PortfolioError) as exc:
        accounts_from_env({"AKD_USER_ID": "A", "AKD_PASSWORD": "p", "AKD_USER_ID_1": "B", "AKD_PASSWORD_1": "q"})
    assert exc.value.code == "invalid_config"


def test_accounts_from_env_skips_blank_user_ids_and_keeps_a_missing_password_as_blank():
    env = {"AKD_USER_ID": "  ", "AKD_PASSWORD": "x", "AKD_USER_ID_2": "B"}
    assert accounts_from_env(env) == [("B", "")]


# --- the command ----------------------------------------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"AKD_PASSWORD": SECRET}, {"AKD_USER_ID": "  ", "AKD_PASSWORD": SECRET}])
def test_no_account_in_env_exits_2_without_network(env):
    http = FakeHTTP()
    code, out, _ = _run(env, http)
    assert code == 2
    doc = json.loads(out)
    assert doc["error"] == "missing_credentials"
    assert "AKD_USER_ID" in doc["message"] and "AKD_PASSWORD" in doc["message"]
    assert http.requests == []


def test_user_without_password_is_a_per_account_error_and_never_hits_the_network():
    http = FakeHTTP()
    code, out, _ = _run({"AKD_USER_ID": ACCOUNT}, http)
    assert code == 2
    entry_ = json.loads(out)["accounts"][0]
    assert entry_["account"] == ACCOUNT and entry_["error"] == "missing_credentials"
    assert http.requests == []


def test_invalid_config_exits_2_without_network():
    http = FakeHTTP()
    env = {"AKD_USER_ID": "A", "AKD_PASSWORD": "p", "AKD_USER_ID_1": "B", "AKD_PASSWORD_1": "q"}
    code, out, _ = _run(env, http)
    assert code == 2 and json.loads(out)["error"] == "invalid_config"
    assert http.requests == []


def test_success_prints_one_json_document_exit_0():
    code, out, _ = _run(ENV, FakeHTTP())
    assert code == 0
    doc = json.loads(out)  # exactly one document: json.loads rejects trailing text
    assert doc["accounts"][0]["account"] == ACCOUNT
    assert doc["accounts"][0]["holdings"]["data"]["positions"][0]["symbol"] == "OGDC"


def test_two_accounts_are_fetched_in_order_with_one_login_each():
    fakes = {ACCOUNT: FakeHTTP(), SECOND: FakeHTTP()}
    env = {**ENV, "AKD_USER_ID_2": SECOND, "AKD_PASSWORD_2": SECRET2}
    code, out, _ = _run(env, http_for=lambda user: fakes[user])
    assert code == 0
    assert [a["account"] for a in json.loads(out)["accounts"]] == [ACCOUNT, SECOND]
    for fake in fakes.values():
        assert sum("SecurecustomAuthentication" in r for r in fake.requests) == 1


def test_one_rejected_account_does_not_stop_the_next_one():
    fakes = {ACCOUNT: FakeHTTP(auth=REJECTED_AUTH), SECOND: FakeHTTP()}
    env = {**ENV, "AKD_USER_ID_2": SECOND, "AKD_PASSWORD_2": SECRET2}
    code, out, _ = _run(env, http_for=lambda user: fakes[user])
    first, second = json.loads(out)["accounts"]
    assert code == 3  # the first failure's code
    assert first["error"] == "login_rejected" and "holdings" not in first
    assert len(fakes[ACCOUNT].requests) == 1  # no retry
    assert second["holdings"]["data"]["positions"][0]["symbol"] == "OGDC"


def test_the_same_account_listed_twice_is_logged_into_once():
    fakes = {ACCOUNT: FakeHTTP()}
    env = {**ENV, "AKD_USER_ID_2": ACCOUNT, "AKD_PASSWORD_2": SECRET}
    code, out, _ = _run(env, http_for=lambda user: fakes[user])
    entries = json.loads(out)["accounts"]
    assert code == 2
    assert entries[0]["account"] == ACCOUNT and "holdings" in entries[0]
    assert entries[1]["error"] == "duplicate_account"
    assert sum("SecurecustomAuthentication" in r for r in fakes[ACCOUNT].requests) == 1


def test_rejected_login_exit_3_one_request():
    http = FakeHTTP(auth=REJECTED_AUTH)
    code, out, _ = _run(ENV, http)
    assert code == 3
    assert json.loads(out)["accounts"][0]["error"] == "login_rejected"
    assert len(http.requests) == 1


def test_unreachable_exit_4():
    code, out, _ = _run(ENV, FakeHTTP(error=httpx.ConnectError("boom")))
    assert code == 4
    assert json.loads(out)["accounts"][0]["error"] == "akd_unreachable"


def test_no_password_ever_in_stdout_or_stderr_even_with_info_logging(caplog):
    env = {**ENV, "AKD_USER_ID_2": SECOND, "AKD_PASSWORD_2": SECRET2}
    with caplog.at_level(logging.INFO):
        for http in (FakeHTTP(), FakeHTTP(auth=REJECTED_AUTH), FakeHTTP(error=httpx.ConnectError("boom"))):
            code, out, err = _run(env, http)
            for secret in (SECRET, SECRET2):
                assert secret not in out and secret not in err
    assert SECRET not in caplog.text and SECRET2 not in caplog.text
    # the daemon's login diagnostic (account id, password length) is not emitted by this command
    assert "AKD login diagnostic" not in caplog.text


def test_main_without_subcommand_prints_usage_and_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["akd"])
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 2
    assert "portfolio" in capsys.readouterr().err


def test_main_portfolio_subcommand_exits_with_command_code(monkeypatch):
    monkeypatch.setattr(entry, "run_portfolio", lambda env, out, err, **k: 7)
    monkeypatch.setattr(sys, "argv", ["akd", "portfolio"])
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 7
