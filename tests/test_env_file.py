from __future__ import annotations

from akd.config import load_env_file


def test_parses_comments_blank_lines_quotes_and_export(tmp_path, monkeypatch):
    for k in ("A_ONE", "A_TWO", "A_THREE", "A_FOUR"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text('# comment\n\nA_ONE=1\nA_TWO="two words"\nexport A_THREE=3\nnot a pair\nA_FOUR=\'x\'\n')
    loaded = load_env_file(f)
    import os
    assert loaded == {"A_ONE": "1", "A_TWO": "two words", "A_THREE": "3", "A_FOUR": "x"}
    assert os.environ["A_TWO"] == "two words"
    for k in loaded:
        monkeypatch.delenv(k, raising=False)


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("A_WIN", "from-env")
    f = tmp_path / ".env"
    f.write_text("A_WIN=from-file\n")
    assert load_env_file(f) == {}
    assert os.environ["A_WIN"] == "from-env"


def test_missing_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == {}


def test_example_file_is_valid_and_has_safe_defaults(tmp_path, monkeypatch):
    """.env.example must load cleanly and must NOT ship a password."""
    import os
    from pathlib import Path
    for k in ("AKD_USER_ID", "AKD_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    loaded = load_env_file(Path(__file__).parents[1] / ".env.example")
    assert "AKD_PASSWORD" not in loaded and "AKD_USER_ID" not in loaded
    for k in loaded:
        os.environ.pop(k, None)
