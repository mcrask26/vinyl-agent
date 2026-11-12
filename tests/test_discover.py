"""Discover: names, taste profile, recommendations, Spotify lists, Missing Pieces (all APIs faked)."""

import pytest

from core import discover, storage
from core.config import MissingKeyError
from core.names import album_matches, norm_artist
from tools import claude, discogs, ebay, lastfm, musicbrainz, spotify


def add(artist, album, year=1971, styles=("Hard Rock",), genres=("Rock",)):
    rid = storage.add_record(artist, album, media_condition="Very Good Plus (VG+)")
    storage.update_record(rid, release={"artist": artist, "album": album, "year": year, "styles": list(styles),
                                        "genres": list(genres)})
    return rid


@pytest.fixture
def shelf():
    add("Led Zeppelin", "Led Zeppelin IV")
    add("Led Zeppelin", "Houses of the Holy", 1973)
    add("Deep Purple", "Machine Head", 1972)
    add("The Beatles", "Abbey Road", 1969, styles=("Pop Rock",))
    return storage.list_records()


@pytest.fixture
def no_ebay(monkeypatch):
    def nokeys(*a, **k):
        raise MissingKeyError("no eBay keys")
    monkeypatch.setattr(ebay, "search_listings", nokeys)


def test_names():
    assert norm_artist("The Beatles") == norm_artist("Beatles") == norm_artist("beatles")
    assert norm_artist("Simon & Garfunkel") == norm_artist("Simon and Garfunkel")
    assert norm_artist("Nirvana (2)") == "nirvana"
    assert album_matches("Abbey Road (Remastered)", "Abbey Road")
    assert album_matches("Rumours - 2004 Remaster", "Rumours")
    assert not album_matches("Led Zeppelin II", "Led Zeppelin III")
    assert not album_matches("Chicago II", "Chicago")
    assert album_matches("[Led Zeppelin IV]", "Led Zeppelin IV")
    assert album_matches("Sgt. Pepper's Lonely Hearts Club Band", "Sgt Peppers Lonely Hearts Club Band")


def test_taste_profile(shelf):
    p = discover.taste_profile(shelf)
    assert p["artists"][0] == ("Led Zeppelin", 2)
    assert p["styles"]["Hard Rock"] == 3 and p["decades"]["1970s"] == 3 and p["count"] == 4


def test_recommend(shelf, monkeypatch, no_ebay):
    similar = {"Led Zeppelin": [{"name": "Deep Purple", "match": 1.0, "url": ""},       # owned: skipped
                                {"name": "Black Sabbath", "match": 0.9, "url": ""},
                                {"name": "The Who", "match": 0.5, "url": ""}],
               "Deep Purple": [{"name": "Black Sabbath", "match": 0.8, "url": ""},
                               {"name": "Uriah Heep", "match": 0.9, "url": ""}],
               "The Beatles": [{"name": "The Who", "match": 0.6, "url": ""}]}
    monkeypatch.setattr(lastfm, "similar_artists", lambda name, limit=20: similar.get(name, []))
    albums = {"Black Sabbath": [{"name": "Greatest Hits", "playcount": 9, "image": ""},
                                {"name": "Paranoid", "playcount": 5, "image": "img"}],
              "The Who": [{"name": "Who's Next", "playcount": 5, "image": ""}],
              "Uriah Heep": [{"name": "Demons and Wizards", "playcount": 5, "image": ""}]}
    monkeypatch.setattr(lastfm, "top_albums", lambda name, limit=8: albums.get(name, []))
    masters = {"Paranoid": dict(master_id=1, album="Paranoid", year=1970, genres=["Rock"], styles=["Hard Rock"]),
               "Who's Next": dict(master_id=2, album="Who's Next", year=1971, genres=["Rock"], styles=["Mod"])}
    monkeypatch.setattr(discogs, "find_master_candidates", lambda a, al, limit=6: [
        dict(masters[al], artist_match=True, unofficial=False, thumb="", url=f"https://discogs.com/master/{al}")]
        if al in masters else [])
    monkeypatch.setattr(discogs, "vinyl_versions", lambda mid: [{"release_id": 9, "country": "UK", "year": "1970",
                                                                 "catno": "VO 3"}])
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": 12.0, "currency": "USD",
                                                                       "num_for_sale": 30})
    result = discover.recommend(shelf, [], n=3)
    names = [i["artist"] for i in result["items"]]
    assert names[0] == "Black Sabbath" and "Deep Purple" not in names
    top = result["items"][0]
    assert top["album"] == "Paranoid"  # compilation skipped
    assert "Led Zeppelin (you own Houses of the Holy, Led Zeppelin IV)" in top["reason"]
    assert "Hard Rock, like 3 of your records" in top["reason"]
    assert top["prices"]["discogs_lowest"] == 12.0 and "eBay keys" in top["prices"]["notes"][0]
    # Answers are cached: a second run makes no Last.fm calls
    monkeypatch.setattr(lastfm, "similar_artists", lambda *a, **k: pytest.fail("should be cached"))
    discover.recommend(shelf, [], n=3)
    # Not interested hides an artist
    storage.hide_suggestion("artist", norm_artist("Black Sabbath"))
    assert "Black Sabbath" not in [i["artist"] for i in discover.recommend(shelf, [], n=3)["items"]]


def test_recommend_errors(monkeypatch):
    with pytest.raises(discover.DiscoverError, match="Add some records"):
        discover.recommend([], [])
    add("Led Zeppelin", "IV")

    def bad_key(*a, **k):
        raise lastfm.LastfmError("Last.fm rejected your API key.", fatal=True)
    monkeypatch.setattr(lastfm, "similar_artists", bad_key)
    with pytest.raises(discover.DiscoverError, match="rejected"):
        discover.recommend(storage.list_records(), [])


def test_add_to_wishlist_links_discogs_and_no_duplicates(monkeypatch):
    monkeypatch.setattr(discogs, "find_master_candidates", lambda a, al, limit=6: [
        dict(master_id=7, album="Paranoid", year=1970, genres=[], styles=[], thumb="t", url="u",
             artist_match=True, unofficial=False)])
    first = discover.add_to_wishlist("Black Sabbath", "Paranoid")
    assert first["linked"] and not first["already"]
    wish = storage.get_wish(first["id"])
    assert wish["master_id"] == 7 and wish["max_price"] is None and wish["min_condition"] == "Very Good Plus (VG+)"
    assert discover.add_to_wishlist("Black Sabbath", "Paranoid (Remastered)")["already"]


def test_spotify_lists(shelf):
    artist = lambda n: {"name": n, "genres": [], "image": "", "url": ""}  # noqa: E731
    track = lambda n, album: {"name": "x", "artists": [n], "album": album, "album_type": "album",  # noqa: E731
                              "year": "1970", "image": "", "url": ""}
    top = {"artists": {"short_term": [artist("Led Zeppelin"), artist("Tame Impala")],
                       "medium_term": [artist("Beatles")], "long_term": []},
           "tracks": {"short_term": [track("Tame Impala", "Currents"), track("Tame Impala", "Currents"),
                                     track("Led Zeppelin", "Physical Graffiti")], "medium_term": [], "long_term": []}}
    lists = discover.spotify_lists(shelf, top)
    assert [e["name"] for e in lists["both"]] == ["Led Zeppelin", "Beatles"]
    zep = lists["both"][0]
    assert zep["top_album"] == "Physical Graffiti" and not zep["owned_top_album"]
    assert lists["streamed_only"][0]["name"] == "Tame Impala" and lists["streamed_only"][0]["top_album"] == "Currents"
    assert [e["name"] for e in lists["owned_only"]] == ["Deep Purple"]


def test_spotify_login_link(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "abc")
    login = spotify.new_login()
    assert "code_challenge_method=S256" in login["url"] and "scope=user-top-read" in login["url"]
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8501%2Fdiscover" in login["url"]
    assert len(login["verifier"]) >= 43


def test_missing_pieces(shelf, monkeypatch):
    monkeypatch.setattr(musicbrainz, "find_artist", lambda name: {"id": "zep", "name": name, "disambiguation": ""})
    groups = [{"id": "1", "title": "Led Zeppelin IV", "primary-type": "Album", "secondary-types": [],
               "first-release-date": "1971-11-08"},
              {"id": "2", "title": "Physical Graffiti", "primary-type": "Album", "secondary-types": [],
               "first-release-date": "1975-02-24"},
              {"id": "3", "title": "Houses of the Holy", "primary-type": "Album", "secondary-types": [],
               "first-release-date": "1973"},
              {"id": "4", "title": "The Song Remains the Same", "primary-type": "Album",
               "secondary-types": ["Live", "Soundtrack"], "first-release-date": "1976"},
              {"id": "5", "title": "Presence", "primary-type": "Album", "secondary-types": [],
               "first-release-date": "1976"}]
    monkeypatch.setattr(musicbrainz, "studio_albums", lambda aid: musicbrainz.parse_release_groups(groups))
    storage.add_wish("Led Zeppelin", "Presence", "Very Good (VG)")
    result = discover.missing_pieces(storage.list_records(), storage.list_wishes())
    assert result["eligible"] == 1  # only Led Zeppelin has 2+ records
    status = {a["title"]: a["status"] for a in result["artists"][0]["albums"]}
    assert status == {"Led Zeppelin IV": "owned", "Houses of the Holy": "owned",
                      "Physical Graffiti": "missing", "Presence": "wishlist"}


def test_lastfm_parsing_and_errors(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setattr(lastfm._limiter, "wait", lambda: None)
    monkeypatch.setattr(lastfm.time, "sleep", lambda s: None)

    class R:
        def __init__(self, body, status=200):
            self._b, self.status_code, self.ok = body, status, status < 400

        def json(self):
            return self._b
    queue = [R({"similarartists": {"artist": {"name": "Black Sabbath", "match": "0.9", "url": "u"}}}),
             R({"error": 29, "message": "Rate limit"}, 429),
             R({"topalbums": {"album": [{"name": "Paranoid", "playcount": "5",
                                         "image": [{"size": "large", "#text": "img"}]}]}}),
             R({"error": 10, "message": "Invalid API key"}, 403)]
    monkeypatch.setattr(lastfm.requests, "get", lambda *a, **k: queue.pop(0))
    assert lastfm.similar_artists("Led Zeppelin") == [{"name": "Black Sabbath", "match": 0.9, "url": "u"}]
    assert lastfm.top_albums("Black Sabbath")[0]["image"] == "img"  # after one retry
    with pytest.raises(lastfm.LastfmError, match="rejected") as exc:
        lastfm.top_albums("x")
    assert exc.value.fatal


def test_claude_is_optional(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("core.config._SECRETS_FILES", ())
    assert not claude.enabled() and claude.rewrite_reasons(["a"]) is None
