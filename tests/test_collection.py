"""Collection workflow with Discogs faked out, using a throwaway database."""

import pytest

from core import collection, storage
from tests.conftest import load_fixture
from tools import discogs

PRICES = discogs.parse_price_suggestions(load_fixture("discogs_price_suggestions.json"))


@pytest.fixture
def fake_discogs(monkeypatch):
    monkeypatch.setattr(discogs, "get_release", lambda rid: dict(discogs.parse_release(load_fixture("discogs_release.json")), release_id=rid))
    monkeypatch.setattr(discogs, "get_price_suggestions", lambda rid: PRICES)
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": 12.0, "currency": "USD", "num_for_sale": 4})


def test_add_match_and_value(fake_discogs):
    rid = storage.add_record("Voodoo Idols", "Temptation", "", "Very Good Plus (VG+)", "Very Good (VG)")
    rec = collection.confirm_match(rid, 1528418)
    assert rec["release_id"] == 1528418 and rec["release"]["year"] == 1984
    assert rec["value"]["typical"] == 30.26 and rec["market"]["num_for_sale"] == 4
    assert storage.price_history(1528418)[0]["typical"] == 30.26


def test_condition_change_reestimates_without_api(fake_discogs, monkeypatch):
    rid = storage.add_record("A", "B", "", "Very Good Plus (VG+)", None)
    collection.confirm_match(rid, 1)
    monkeypatch.setattr(discogs, "get_price_suggestions", lambda rid: pytest.fail("should not call the API"))
    collection.change_condition(rid, "Near Mint (NM or M-)", "Generic", "note")
    assert storage.get_record(rid)["value"]["typical"] == 45.5


def test_refresh_all_saves_snapshot_and_skips_unmatched(fake_discogs):
    collection.confirm_match(storage.add_record("A", "B", "", "Very Good (VG)", None), 1)
    storage.add_record("Unmatched", "Record", "", "Very Good (VG)", None)
    result = collection.refresh_all()
    assert result["refreshed"] == 1 and result["unmatched"] == 1 and result["snapshot_saved"]
    snap = storage.list_snapshots()[0]
    assert snap["total_typical"] == 18.0 and snap["unvalued_count"] == 1


def test_refresh_stops_on_fatal_error(fake_discogs, monkeypatch):
    collection.confirm_match(storage.add_record("A", "B", "", "Very Good (VG)", None), 1)

    def down(rid):
        raise discogs.DiscogsError("Couldn't reach Discogs", fatal=True)

    monkeypatch.setattr(discogs, "get_price_suggestions", down)
    with pytest.raises(discogs.DiscogsError):
        collection.refresh_all()
    assert storage.list_snapshots() == []


def test_refresh_keeps_going_past_one_missing_release(fake_discogs, monkeypatch):
    first = storage.add_record("A", "B", "", "Very Good (VG)", None)
    collection.confirm_match(first, 1)
    collection.confirm_match(storage.add_record("C", "D", "", "Very Good (VG)", None), 2)

    def flaky(rid):
        if rid == 1:
            raise discogs.DiscogsNotFound("gone")
        return PRICES

    monkeypatch.setattr(discogs, "get_price_suggestions", flaky)
    result = collection.refresh_all()
    assert result["refreshed"] == 1 and len(result["failures"]) == 1
    assert storage.get_record(first)["value"]["typical"] == 18.0  # previous value kept


def test_prices_locked_gives_unknown_value_but_keeps_listings(fake_discogs, monkeypatch):
    calls = []

    def locked(rid):
        calls.append(rid)
        raise discogs.DiscogsPricesUnavailable("finish seller setup")

    monkeypatch.setattr(discogs, "get_price_suggestions", locked)
    a = storage.add_record("A", "B", "", "Very Good (VG)", None)
    rec = collection.confirm_match(a, 1)  # no exception: the match is saved
    assert rec["release_id"] == 1 and rec["value"]["locked"] and rec["value"]["typical"] is None
    assert rec["market"]["num_for_sale"] == 4
    collection.confirm_match(storage.add_record("C", "D", "", "Very Good (VG)", None), 2)
    calls.clear()
    result = collection.refresh_all()
    assert result["prices_locked"] and result["refreshed"] == 2 and not result["snapshot_saved"]
    assert calls == [1]  # asked once, then skipped for the rest
