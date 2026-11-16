"""Claude features with the API faked: readings, picks, fallbacks and spend tracking."""

import json
from types import SimpleNamespace

import pytest

from core import deals, discover, storage, wishlist
from tools import claude, ebay

VGP, VG, NM = "Very Good Plus (VG+)", "Very Good (VG)", "Near Mint (NM or M-)"


def fake_response(data, stop="end_turn", tokens=(1000, 200)):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=json.dumps(data))],
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


@pytest.fixture
def fake_claude(monkeypatch):
    """Turns Claude 'on' with a fake client. Returns (requests made, queue of answers)."""
    import anthropic

    calls, queue = [], []

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class Client:
        def __init__(self, **kw):
            self.messages = Messages()
            self.beta = SimpleNamespace(messages=self.messages)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", Client)
    return calls, queue


def listing(i, title, description="", price=20.0):
    return {"source": "eBay", "listing_id": f"id{i}", "title": title, "description": description, "price": price,
            "shipping": 4.0, "currency": "USD", "auction": False, "url": f"https://ebay.com/itm/{i}",
            "ebay_condition": "Used", "aspects": {}, "seller_feedback_pct": 99.5, "seller_feedback_score": 500}


def test_off_without_a_key():
    assert not claude.enabled()
    assert claude.read_listings("A", "B", [listing(1, "x")]) == (None, "No Claude key is set.")
    assert claude.pick_records({}, [{}] * 8)[0] is None


def test_read_listings_and_spend(fake_claude):
    calls, queue = fake_claude
    queue.append(fake_response({"listings": [
        {"id": "id1", "record_grade": VGP, "sleeve_grade": VG, "evidence": "plays great, light marks", "concern": ""},
        {"id": "id2", "record_grade": "unknown", "sleeve_grade": "unknown", "evidence": "", "concern": "This is a CD."},
        {"id": "made-up", "record_grade": NM, "sleeve_grade": NM, "evidence": "", "concern": ""}]}))
    out, problem = claude.read_listings("Fleetwood Mac", "Rumours", [listing(1, "Rumours LP"), listing(2, "Rumours")])
    assert problem is None and set(out) == {"id1", "id2"}  # the made-up id is dropped
    assert out["id1"]["media"] == VGP and out["id2"]["media"] is None and out["id2"]["concern"] == "This is a CD."
    sent = calls[0]
    assert sent["model"] == "claude-haiku-5-5" and sent["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in sent and "temperature" not in sent
    used = claude.usage()
    assert used["calls"] == 1 and used["cost"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1_000_000)


def test_problems_fall_back_cleanly(fake_claude):
    import anthropic

    _, queue = fake_claude
    resp = SimpleNamespace(status_code=401, headers={}, request=None)
    queue.append(anthropic.AuthenticationError("bad key", response=resp, body=None))
    out, problem = claude.read_listings("A", "B", [listing(1, "x")])
    assert out is None and "rejected your key" in problem
    queue.append(fake_response({}, stop="refusal"))
    assert claude.read_listings("A", "B", [listing(1, "x")]) == (None, "Claude declined this request.")
    queue.append(SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="not json")],
                                 usage=SimpleNamespace(input_tokens=1, output_tokens=1)))
    assert "expected form" in claude.read_listings("A", "B", [listing(1, "x")])[1]


def test_reading_changes_grade_but_sellers_field_wins():
    rules = {"media": None, "sleeve": None, "evidence": ""}
    got = deals.apply_reading(rules, {"media": VGP, "sleeve": VG, "evidence": "light marks", "concern": ""})
    assert got["media"] == VGP and got["read_by"] == "Claude" and "light marks" in got["evidence"]
    seller = {"media": VG, "sleeve": None, "evidence": "seller's Record Grading: VG"}
    got = deals.apply_reading(seller, {"media": NM, "sleeve": NM, "evidence": "x", "concern": "Mentions a skip."})
    assert got["media"] == VG and got["read_by"] == "rules" and got["concern"] == "Mentions a skip."
    assert deals.apply_reading(rules, None)["read_by"] == "rules"
    # Claude found nothing: the rules' grade stays
    assert deals.apply_reading(dict(rules, media=VG), {"media": None, "concern": ""})["media"] == VG


def test_search_uses_claude_once_per_listing(fake_claude, monkeypatch):
    calls, queue = fake_claude
    found = [listing(1, "Rumours LP nice copy", "Plays great with light marks", 18),
             listing(2, "Rumours LP VG+", "skips on side 2", 15)]
    monkeypatch.setattr(ebay, "search_listings", lambda *a, **k: [dict(l) for l in found])
    monkeypatch.setattr(ebay, "add_details", lambda l: l)
    monkeypatch.setattr(wishlist, "_discogs_row", lambda wish, errors: None)
    wid = storage.add_wish("Fleetwood Mac", "Rumours", max_price=30, min_condition=VG)
    queue.append(fake_response({"listings": [
        {"id": "id1", "record_grade": VGP, "sleeve_grade": "unknown", "evidence": "light marks", "concern": ""},
        {"id": "id2", "record_grade": VGP, "sleeve_grade": "unknown", "evidence": "VG+",
         "concern": "Graded VG+ but the description says it skips."}]}))
    results = {l["listing_id"]: l for l in wishlist.search_wish(wid)["results"]}
    assert results["id1"]["grade"] == VGP and results["id1"]["read_by"] == "Claude" and results["id1"]["meets"]
    assert any("Claude noticed" in f for f in results["id2"]["flags"]) and results["id2"]["suspicious"]
    wishlist.search_wish(wid)                      # same listings again: answered from the saved readings
    assert len(calls) == 1
    wishlist.search_wish(wid, use_claude=False)    # the daily check's default
    assert len(calls) == 1


def test_claude_problem_keeps_the_search_working(fake_claude, monkeypatch):
    import anthropic

    _, queue = fake_claude
    monkeypatch.setattr(ebay, "search_listings", lambda *a, **k: [listing(1, "Rumours LP VG+")])
    monkeypatch.setattr(ebay, "add_details", lambda l: l)
    monkeypatch.setattr(wishlist, "_discogs_row", lambda wish, errors: None)
    wid = storage.add_wish("Fleetwood Mac", "Rumours", max_price=30, min_condition=VG)
    queue.append(anthropic.APIConnectionError(request=None))
    wish = wishlist.search_wish(wid)
    assert wish["results"][0]["grade"] == VGP and wish["results"][0]["read_by"] == "rules"
    note = wish["errors"][0]
    assert note["source"] == "Claude" and note["note"] and "built-in rules" in note["message"]


def test_pick_records_validates_the_answer(fake_claude):
    calls, queue = fake_claude
    cands = [{"artist": f"A{i}", "album": f"B{i}"} for i in range(8)]
    queue.append(fake_response({"picks": [{"number": n, "reason": f"Because {n}."} for n in (3, 1, 8, 5, 2)]}))
    picks, problem = claude.pick_records({"records_owned": 157}, cands)
    assert problem is None and [p["index"] for p in picks] == [2, 0, 7, 4, 1]
    assert calls[0]["model"] == "claude-sonnet-5-5" and calls[0]["fallbacks"] == "default"
    # repeats and out-of-range numbers don't count, so this answer is rejected
    queue.append(fake_response({"picks": [{"number": n, "reason": "x"} for n in (1, 1, 99, 2, 3)]}))
    assert claude.pick_records({}, cands) == (None, "Claude's picks weren't usable.")


def test_recommend_with_picker_and_fallback(monkeypatch):
    records = [{"id": i, "artist": a, "album": f"Album {i}", "release_id": i,
                "release": {"year": 1970, "styles": ["Folk Rock"], "genres": ["Rock"]}}
               for i, a in enumerate(["Bob Dylan", "Bob Dylan", "The Band"])]
    similar = [{"name": f"Artist {i}", "match": 1 - i / 20} for i in range(8)]
    monkeypatch.setattr(discover, "_similar", lambda name: similar)
    monkeypatch.setattr(discover, "_top_album", lambda artist: {"name": f"Best of {artist}", "image": ""})
    monkeypatch.setattr(discover, "_discogs_album", lambda a, b: None)
    monkeypatch.setattr(discover, "cached_price_check", lambda *a: None)

    def picker(summary, facts, n):
        assert summary["records_owned"] == 3 and facts[0]["similar_to_artists_they_own"]
        return [{"index": i, "reason": f"Claude likes {facts[i]['artist']}."} for i in (7, 6, 5, 4, 3)], None

    out = discover.recommend(records, [], picker=picker)
    assert out["picked_by"] == "Claude" and out["items"][0]["artist"] == "Artist 7"
    assert out["items"][0]["reason"] == "Claude likes Artist 7."
    out = discover.recommend(records, [], picker=lambda *a: (None, "Your Claude credit has run out."))
    assert out["picked_by"] == "rules" and out["items"][0]["artist"] == "Artist 0"
    assert "credit has run out" in out["notes"][-1] and out["items"][0]["reason"].startswith("Fans of")
