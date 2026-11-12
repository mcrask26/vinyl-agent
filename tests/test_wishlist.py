"""Wishlist search with eBay and Discogs faked out."""

from core import storage, wishlist
from core.config import MissingKeyError
from tools import discogs, ebay


def ebay_listing(i, price, title="Fleetwood Mac Rumours LP VG+/VG"):
    return {"source": "eBay", "listing_id": f"e{i}", "title": title, "url": f"https://ebay.com/itm/{i}",
            "image": "", "price": price, "shipping": 5.0, "shipping_note": None, "currency": "USD",
            "auction": False, "seller_feedback_pct": 99.9, "seller_feedback_score": 800, "description": "",
            "aspects": {}, "ebay_condition": "Used"}


def test_search_wish_rates_and_saves(monkeypatch):
    monkeypatch.setattr(ebay, "search_listings", lambda *a, **k: [ebay_listing(i, p) for i, p in
                                                                   enumerate([20, 25, 27, 29, 31, 33])])
    monkeypatch.setattr(ebay, "add_details", lambda l: l)
    monkeypatch.setattr(discogs, "vinyl_versions", lambda mid: [{"release_id": 9, "country": "US", "year": "1977", "catno": "BSK 3010"}])
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": 18.0, "currency": "USD", "num_for_sale": 40})
    wid = storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", 40, master_id=123)
    w = wishlist.search_wish(wid)
    assert len(w["results"]) == 7 and w["errors"] == [] and w["checked_at"]
    great = [l for l in w["results"] if l["rating"] == "Great Deal"]
    assert great and great[0]["title"].endswith("VG+/VG") and great[0]["meets"]
    discogs_row = next(l for l in w["results"] if l["source"] == "Discogs")
    assert not discogs_row["meets"]  # its condition isn't known
    assert len(storage.listing_price_history(wid)) == 7


def test_missing_ebay_keys_still_uses_discogs(monkeypatch):
    def no_keys(*a, **k):
        raise MissingKeyError("eBay keys aren't set")
    monkeypatch.setattr(ebay, "search_listings", no_keys)
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": 18.0, "currency": "USD", "num_for_sale": 3})
    monkeypatch.setattr(discogs, "get_price_suggestions", lambda rid: (_ for _ in ()).throw(discogs.DiscogsPricesUnavailable("x")))
    wid = storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", None, release_id=55)
    w = wishlist.search_wish(wid)
    assert w["errors"][0]["setup"] and len(w["results"]) == 1
    assert w["results"][0]["rating"] == "Not rated"


def test_any_pressing_uses_vinyl_pressings_not_the_album_price(monkeypatch):
    """The album-level Discogs price counts CDs too; we price the common vinyl pressings instead."""
    versions = [{"release_id": 1, "country": "US", "year": "1977", "catno": "BSK 3010"},
                {"release_id": 2, "country": "UK", "year": "1977", "catno": "K 56344"},
                {"release_id": 3, "country": "DE", "year": "1977", "catno": ""}]
    calls = []
    monkeypatch.setattr(discogs, "vinyl_versions", lambda mid: calls.append(mid) or versions)
    prices = {1: (11.47, 28), 2: (6.76, 60), 3: (None, 0)}
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {
        "lowest_price": prices[rid][0], "currency": "USD", "num_for_sale": prices[rid][1]})
    best = wishlist.cheapest_vinyl(38722)
    assert best["lowest_price"] == 6.76 and best["release_id"] == 2 and best["num_for_sale"] == 88
    assert best["pressing"] == "UK 1977 (K 56344)"

    def no_keys(*a, **k):
        raise MissingKeyError("no eBay")
    monkeypatch.setattr(ebay, "search_listings", no_keys)
    wid = storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", None, master_id=38722)
    row = wishlist.search_wish(wid)["results"][0]
    assert row["total"] == 6.76 and "UK 1977 (K 56344) pressing (60 for sale)" in row["title"]
    assert row["url"] == "https://www.discogs.com/sell/release/2?sort=price%2Casc"  # the same copy, cheapest first
    assert calls == [38722]  # pressing list was saved, not fetched again
