import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Every test gets its own empty database, never your real one."""
    monkeypatch.setenv("VINYL_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "LASTFM_API_KEY", "SPOTIFY_CLIENT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "")  # tests never use your real keys or call the real services
    yield
