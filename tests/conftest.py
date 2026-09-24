from __future__ import annotations

import pytest

from akd import credentials
from akd.store import SnapshotStore


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Never touch the real ./data directory or a real saved session."""
    from akd import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "akd.db")
    monkeypatch.delenv("AKD_PASSWORD", raising=False)
    credentials._creds = None
    yield
    credentials._creds = None


@pytest.fixture
def store(tmp_path):
    s = SnapshotStore(tmp_path / "t.db")
    yield s
    s.close()
