import pytest
import requests

from tests.conftest import load_fixture
from tools import discogs


class FakeResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def fake_get(monkeypatch):
    """Replace requests.get with a queue of canned responses; records the calls made."""
    calls, queue = [], []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(discogs.requests, "get", _get)
    monkeypatch.setattr(discogs.time, "sleep", lambda s: None)
    monkeypatch.setattr(discogs._limiter, "wait", lambda: None)
    return calls, queue


def test_parse_release_ref():
    assert discogs.parse_release_ref("https://www.discogs.com/release/1234567-Led-Zeppelin-IV") == 1234567
    assert discogs.parse_release_ref("[r42]") == 42
    assert discogs.parse_release_ref(" 99 ") == 99
    with pytest.raises(ValueError, match="master"):
        discogs.parse_release_ref("https://www.discogs.com/master/123-Album")
    with pytest.raises(ValueError):
        discogs.parse_release_ref("hello")


def test_search_ranks_catalog_match_first():
    results = [discogs.parse_search_result(r, "SD-7208") for r in load_fixture("discogs_search.json")["results"]]
    ranked = discogs.rank_candidates(results)
    assert ranked[0]["release_id"] == 222 and ranked[0]["catno_match"]
    # then most-owned first
    assert [r["release_id"] for r in ranked[1:]] == [333, 111]
    third = next(r for r in results if r["release_id"] == 333)
    assert third["artist"] == "Led Zeppelin"  # " (2)" disambiguation removed
    assert third["year"] is None
    assert results[0]["labels"] == ["Atlantic"]  # duplicates removed
    assert results[0]["url"] == "https://www.discogs.com/release/111-Led-Zeppelin-Untitled"


def test_parse_release():
    rel = discogs.parse_release(load_fixture("discogs_release.json"))
    assert rel["release_id"] == 1528418
    assert rel["artist"] == "Voodoo Idols"
    assert rel["year"] == 1984
    assert rel["catno"] == "NR15765-2"
    assert "LP" in rel["formats"] and "Vinyl" in rel["formats"]
    assert rel["thumb"].endswith("-150.jpg")
    assert rel["lowest_price"] == 10.99


def test_parse_price_suggestions_and_stats():
    prices, currency = discogs.parse_price_suggestions(load_fixture("discogs_price_suggestions.json"))
    assert currency == "USD"
    assert prices["Very Good Plus (VG+)"] == 30.26
    assert discogs.parse_price_suggestions({}) == ({}, None)
    stats = discogs.parse_marketplace_stats({"num_for_sale": 0, "lowest_price": None})
    assert stats == {"lowest_price": None, "currency": None, "num_for_sale": 0}


def test_catno_search_then_album_fallback(fake_get):
    calls, queue = fake_get
    master = {"results": [{"id": 4300, "type": "master", "title": "Led Zeppelin - Untitled", "year": "1971",
                           "uri": "/master/4300", "community": {"have": 400000}, "format": ["Vinyl"]}]}
    pressings = load_fixture("discogs_search.json")
    for r in pressings["results"]:
        r["master_id"] = 4300
    # 1: catno search finds nothing, 2-3: album (master) search, 4: pressings of that album
    queue += [FakeResp(200, {"results": []}), FakeResp(200, master), FakeResp(200, {"results": []}),
              FakeResp(200, pressings)]
    cands, note = discogs.find_release_candidates("Led Zeppelin", "Led Zeppelin IV", "XYZ-1")
    assert len(cands) == 3 and "XYZ-1" in note
    assert calls[0][1]["catno"] == "XYZ-1"
    assert calls[3][1]["release_title"] == "Untitled"
    assert calls[0][2]["Authorization"] == "Discogs token=test-token"
    assert all(c[1]["format"] == "Vinyl" for c in calls)


def test_album_name_differs_note(fake_get):
    _, queue = fake_get
    master = {"results": [{"id": 4300, "type": "master", "title": "Led Zeppelin - Untitled", "year": "1971",
                           "uri": "/master/4300", "community": {"have": 400000}, "format": ["Vinyl"]},
                          {"id": 99, "type": "master", "title": "Led Zeppelin - Bootleg IV", "year": "1990",
                           "uri": "/master/99", "community": {"have": 10}, "format": ["Vinyl", "Unofficial Release"]}]}
    pressings = load_fixture("discogs_search.json")
    for r in pressings["results"]:
        r["master_id"] = 4300
    queue += [FakeResp(200, master), FakeResp(200, {"results": []}), FakeResp(200, pressings)]
    cands, note = discogs.find_release_candidates("Led Zeppelin", "Led Zeppelin IV")
    assert "Untitled" in note and cands[0]["release_id"] == 333  # most-owned first


def test_retries_on_429_then_succeeds(fake_get):
    calls, queue = fake_get
    queue += [FakeResp(429), FakeResp(200, {"num_for_sale": 3, "lowest_price": {"value": 5, "currency": "USD"}})]
    assert discogs.get_marketplace_stats(1)["num_for_sale"] == 3
    assert len(calls) == 2


def test_errors_are_clear(fake_get):
    _, queue = fake_get
    queue.append(FakeResp(401, {"message": "Invalid consumer token."}))
    with pytest.raises(discogs.DiscogsError, match="rejected your token") as exc:
        discogs.get_release(1)
    assert exc.value.fatal
    queue.append(requests.ConnectionError("boom"))
    with pytest.raises(discogs.DiscogsError, match="Couldn't reach Discogs"):
        discogs.get_release(1)
    queue.append(FakeResp(404, {"message": "Release not found."}))
    with pytest.raises(discogs.DiscogsNotFound) as exc:
        discogs.get_release(1)
    assert not exc.value.fatal
    queue.append(FakeResp(403, {"message": "You must fill out your seller settings first."}))
    with pytest.raises(discogs.DiscogsPricesUnavailable, match="settings/seller") as exc:
        discogs.get_price_suggestions(1)
    assert not exc.value.fatal


def test_missing_token(monkeypatch):
    from core.config import MissingKeyError

    monkeypatch.setenv("DISCOGS_TOKEN", "")
    monkeypatch.setattr("core.config._SECRETS_FILES", ())
    with pytest.raises(MissingKeyError, match="DISCOGS_TOKEN"):
        discogs.get_release(1)


def test_title_beats_popularity_when_album_named():
    """A search for "Caribou" must not pick the more-owned "Captain Fantastic"."""
    base = dict(kind="master", release_id=None, catno_match=False, artist_match=True, unofficial=False)
    cf = dict(base, master_id=1, album="Captain Fantastic And The Brown Dirt Cowboy", have=90000)
    caribou = dict(base, master_id=2, album="Caribou", have=30000)
    assert discogs.rank_candidates([cf, caribou], "Caribou")[0]["master_id"] == 2
    assert discogs.rank_candidates([cf, caribou])[0]["master_id"] == 1  # no title given: most-owned
    concert = dict(base, master_id=3, album="Concert", have=9000)
    in_concert = dict(base, master_id=4, album="The Beach Boys In Concert", have=5000)
    assert discogs.rank_candidates([concert, in_concert], "In Concert")[0]["master_id"] == 4
