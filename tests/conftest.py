from __future__ import annotations

import os

# Never let a developer's real .env leak into tests. Must be set before `akd` is imported.
os.environ["AKD_ENV_FILE"] = ""

import pytest  # noqa: E402

from akd.store import SnapshotStore  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Never touch the real ./data directory."""
    from akd import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "akd.db")
    monkeypatch.delenv("AKD_PASSWORD", raising=False)
    yield


@pytest.fixture
def store(tmp_path):
    s = SnapshotStore(tmp_path / "t.db")
    yield s
    s.close()
