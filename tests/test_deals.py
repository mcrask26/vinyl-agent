from core import deals
from tests.conftest import load_fixture
from tools import discogs


def listing(price, shipping=4.0, title="Led Zeppelin IV VG+/VG", pct=99.8, score=500, source="eBay", desc=""):
    return {"source": source, "listing_id": f"{source}-{price}-{title}", "title": title, "price": price,
            "shipping": shipping, "currency": "USD", "seller_feedback_pct": pct, "seller_feedback_score": score,
            "description": desc, "aspects": {}, "url": "https://example.com"}


def comparables():
    return [listing(p) for p in (26, 28, 30, 32, 34, 36)]  # totals 30..40


def test_reference_needs_enough_listings():
    assert deals.reference_from_listings([10, 20, 30, 40]) is None
    ref = deals.reference_from_listings([30, 32, 34, 36, 38, 40])
    assert ref["typical"] == 35.0 and ref["low"] == 32.5 and ref["high"] == 37.5


def test_great_fair_overpriced_with_reason():
    rated = {l["price"]: l for l in deals.evaluate(comparables() + [listing(20), listing(60)], 50, "Very Good (VG)")}
    assert rated[20]["rating"] == deals.GREAT
    assert rated[30]["rating"] == deals.FAIR
    assert rated[60]["rating"] == deals.OVER
    assert rated[60]["reason"].startswith("Listed at $64.00 with shipping; similar copies are listed for $")
    assert rated[20]["meets"] and not rated[60]["meets"]  # $64 is over the $50 max
    assert deals.is_alert_worthy(rated[20])


def test_unrated_without_comparables():
    rated = deals.evaluate([listing(20), listing(30)], None, "Very Good (VG)")
    assert all(l["rating"] == deals.UNRATED for l in rated)
    assert "not enough" in rated[0]["reason"]


def test_discogs_sales_used_when_available():
    prices, _ = discogs.parse_price_suggestions(load_fixture("discogs_price_suggestions.json"))
    rated = deals.evaluate([listing(45, shipping=5)], None, "Very Good (VG)", prices)  # VG+ sells ~$30.26
    assert rated[0]["rating"] == deals.OVER
    assert "VG+ copies usually sell for $27.23 – $33.29" in rated[0]["reason"]


def test_suspicious_flags():
    rated = {l["title"]: l for l in deals.evaluate(comparables() + [
        listing(5, title="Led Zeppelin IV NM cheap"),
        listing(29, title="Led Zeppelin IV VG+ bad seller", pct=91.0),
        listing(29, title="Led Zeppelin IV VG+ new seller", score=2),
        listing(29, title="Led Zeppelin IV NM", desc="Skips on track 2"),
        listing(29, title="Led Zeppelin IV bootleg VG+"),
    ], None, "Very Good (VG)")}
    assert "far below normal" in rated["Led Zeppelin IV NM cheap"]["flags"][0]
    assert not deals.is_alert_worthy(rated["Led Zeppelin IV NM cheap"])
    assert "91% positive" in rated["Led Zeppelin IV VG+ bad seller"]["flags"][0]
    assert "only 2 ratings" in rated["Led Zeppelin IV VG+ new seller"]["flags"][0]
    assert "skips" in rated["Led Zeppelin IV NM"]["flags"][0]
    assert "bootleg" in rated["Led Zeppelin IV bootleg VG+"]["flags"][0]
    assert not rated["Led Zeppelin IV VG+/VG"]["flags"]


def test_condition_limits():
    rated = deals.evaluate(comparables() + [listing(20, title="Led Zeppelin IV G+/G"),
                                            listing(21, title="Led Zeppelin IV original")], None, "Very Good Plus (VG+)")
    by_title = {l["title"]: l for l in rated}
    assert not by_title["Led Zeppelin IV G+/G"]["meets_condition"]
    assert not by_title["Led Zeppelin IV original"]["meets_condition"]  # unknown grade never counts as meeting it
    rechecked = deals.recheck_limits(rated, 10, "Good (G)")
    assert all(not l["meets"] for l in rechecked)  # all over $10
