from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_saved_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
