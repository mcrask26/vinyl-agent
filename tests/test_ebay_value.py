"""Values from eBay listings when Discogs prices are locked (eBay faked)."""

from core import collection, ebay_value, storage, valuation
from core.config import MissingKeyError
from tools import discogs, ebay

NM, VGP, VG, G = "Near Mint (NM or M-)", "Very Good Plus (VG+)", "Very Good (VG)", "Good (G)"


def test_estimate_uses_your_grade_then_neighbors():
    listings = [{"price": p, "grade": VGP} for p in (20, 25, 30)] + [{"price": 80, "grade": NM}, {"price": 5, "grade": G}]
    v = valuation.estimate_from_listings(listings, VGP, "USD", "same catalog number (KCS 9463)")
    assert v["status"] == valuation.OK and (v["low"], v["typical"], v["high"]) == (22.5, 25.0, 27.5)
    assert "3 eBay listings of VG+ copies" in v["reason"] and "asking prices" in v["reason"]
    # Only 1 NM copy: widen to one grade either side (VG+ and NM)
    v = valuation.estimate_from_listings(listings, NM, "USD", "x")
    assert v["count"] == 4 and v["typical"] == 27.5
    # No condition set: VG to NM copies
    assert valuation.estimate_from_listings(listings, None, "USD", "x")["count"] == 4
    # Too few: unknown, never guessed
    v = valuation.estimate_from_listings(listings[:2], VGP, "USD", "x")
    assert v["status"] == valuation.UNKNOWN and v["typical"] is None and "Not enough" in v["reason"]


def fake_listing(i, price, title, auction=False):
    return {"listing_id": str(i), "title": title, "url": f"https://ebay.com/itm/{i}", "price": price, "shipping": 4.0,
            "auction": auction, "description": "", "aspects": {}, "ebay_condition": "Used"}


def test_refresh_values_from_ebay(monkeypatch):
    monkeypatch.setattr(discogs, "get_price_suggestions", lambda rid: (_ for _ in ()).throw(discogs.DiscogsPricesUnavailable("x")))
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": 0.25, "currency": "USD", "num_for_sale": 99})
    found = [fake_listing(1, 40, "Bob Dylan Greatest Hits KCS 9463 LP VG+/VG"),
             fake_listing(2, 50, "Bob Dylan's Greatest Hits KCS-9463 vinyl VG+"),
             fake_listing(3, 60, "Dylan Greatest Hits KCS 9463 VG+ 1967"),
             fake_listing(4, 45, "Bob Dylan Greatest Hits KCS 9463 VG+", auction=True),        # auction: skipped
             fake_listing(5, 70, "Bob Dylan Greatest Hits KCS 9463 NM bootleg"),               # bootleg: skipped
             fake_listing(6, 55, "Bob Dylan Greatest Hits KCS 9463 VG+ skips on side 2"),      # damage: skipped
             fake_listing(7, 99, "Bob Dylan Greatest Hits KCS 9463 sealed reissue NM")]
    monkeypatch.setattr(ebay, "search_listings", lambda *a, **k: found)
    monkeypatch.setattr(ebay, "add_details", lambda l: l)
    rid = storage.add_record("Bob Dylan", "Bob Dylan's Greatest Hits", media_condition=VGP)
    storage.update_record(rid, release_id=5, release={"catno": "KCS 9463", "artist": "Bob Dylan"})
    collection.refresh_all()
    v = storage.get_record(rid)["value"]
    assert v["status"] == valuation.OK and v["typical"] == 50.0 and v["source"] == "ebay"
    assert "same catalog number (KCS 9463)" in v["reason"]
    assert valuation.summarize(storage.list_records())["total_typical"] == 50.0
    # Changing the condition re-estimates from the saved listings, no new eBay calls
    monkeypatch.setattr(ebay, "search_listings", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    collection.change_condition(rid, NM, None, "")
    # No NM copies (the NM bootleg and the sealed reissue don't count for an original): widened to VG+
    assert storage.get_record(rid)["value"]["typical"] == 50.0


def test_no_ebay_keys_stays_unknown(monkeypatch):
    monkeypatch.setattr(discogs, "get_price_suggestions", lambda rid: (_ for _ in ()).throw(discogs.DiscogsPricesUnavailable("x")))
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": 1.0, "currency": "USD", "num_for_sale": 3})

    def no_keys(*a, **k):
        raise MissingKeyError("no eBay")
    monkeypatch.setattr(ebay, "search_listings", no_keys)
    rid = storage.add_record("X", "Y")
    storage.update_record(rid, release_id=1, release={"catno": "A-1"})
    collection.refresh_all()
    assert storage.get_record(rid)["value"]["locked"]


def test_reissue_listings_count_only_for_reissues():
    from core import ebay_value
    l = {"price": 30, "grade": NM, "title": "Thriller 180g reissue NM", "auction": False}
    assert not ebay_value._usable(l, original=True) and ebay_value._usable(l, original=False)


def test_modern_and_various_artist_records():
    assert ebay_value.is_original({"year": 1969, "formats": ["Vinyl", "LP"]})
    assert not ebay_value.is_original({"year": 2023, "formats": ["Vinyl", "LP"]})  # sold sealed: those count
    assert not ebay_value.is_original({"year": 1980, "formats": ["Vinyl", "Reissue"]})
    assert ebay_value.is_original({"year": None, "formats": ["Vinyl"]})
    assert not ebay_value.is_original({"year": None, "formats": ["Vinyl", "Repress"]})
    # A modern record with no condition set is valued from new and Near Mint copies
    new = [{"price": p, "grade": "Mint (M)"} for p in (30, 40, 50)]
    assert valuation.estimate_from_listings(new, None, "USD", "x")["status"] == valuation.UNKNOWN
    v = valuation.estimate_from_listings(new, None, "USD", "x", modern=True)
    assert v["typical"] == 40 and "new" in v["reason"]
    assert ebay_value._search_artist("Various Artists") == "" and ebay_value._search_artist("Wings") == "Wings"
    assert ebay_value._short_album("Woodstock (Soundtrack)") == "Woodstock"
    assert ebay_value._short_album("A Foot in the Door: The Best of Pink Floyd") == "A Foot in the Door"
