"""Bulk import from a list, with Discogs faked."""

from core import storage
from scripts import import_list
from tools import discogs


def cand(rid, album, country="US"):
    return {"release_id": rid, "album": album, "year": 1971, "country": country, "catno": "X-1"}


def test_prefers_us_pressing():
    assert import_list.pick([cand(1, "a", "Europe"), cand(2, "a", "US")])["release_id"] == 2
    assert import_list.pick([cand(1, "a", "UK")])["release_id"] == 1


def test_import_adds_matches_and_skips_repeats(monkeypatch):
    calls = []
    monkeypatch.setattr(discogs, "find_release_candidates",
                        lambda a, al, catno="", limit=12: (calls.append(a) or ([cand(1, al)] if al != "Nope" else []), None))
    monkeypatch.setattr(discogs, "get_release", lambda rid: {"release_id": rid, "artist": "X", "album": "Y"})
    monkeypatch.setattr(discogs, "get_price_suggestions",
                        lambda rid: (_ for _ in ()).throw(discogs.DiscogsPricesUnavailable("locked")))
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": 5.0, "currency": "USD", "num_for_sale": 3})
    rows = [{"artist": "Neil Young", "album": "Harvest"}, {"artist": "Various Artists", "album": "Woodstock"},
            {"artist": "Someone", "album": "Nope"}]
    out = import_list.import_rows(rows, log=lambda m: None)
    assert out["added"] == 3 and out["unmatched"] == ["Someone – Nope"]
    assert "Various" in calls  # Discogs's name for compilations
    recs = {r["album"]: r for r in storage.list_records()}
    assert recs["Harvest"]["release_id"] == 1 and "please check" in recs["Harvest"]["notes"]
    assert recs["Nope"]["release_id"] is None and "Re-match" in recs["Nope"]["notes"]
    # Running again adds nothing
    assert import_list.import_rows(rows, log=lambda m: None)["skipped"] == 3
