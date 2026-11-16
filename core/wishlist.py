"""Wishlist search: gathers listings from eBay and Discogs, rates them, and saves the results.

The page, the "Check now" button and the daily script (step 3) all call search_wish().
"""

import hashlib

from core import deals, storage
from core.config import MissingKeyError
from tools import claude, discogs, ebay

DETAIL_LIMIT = 12  # read the full listing (grading fields, description) for the 12 cheapest
COMMON_PRESSINGS = 8  # for "any pressing" wishes, price the 8 most-owned vinyl pressings on Discogs


def pressing_text(v: dict) -> str:
    return " ".join(x for x in [v.get("country"), v.get("year")] if x) + (f" ({v['catno']})" if v.get("catno") else "")


def cheapest_vinyl(master_id: int, top: int = COMMON_PRESSINGS):
    """Cheapest copy on Discogs among an album's most common vinyl pressings, any condition.

    About top+1 Discogs calls (the pressing list is saved for a week). None if none are for sale.
    """
    versions = storage.cached(f"discogs:vinyl_versions:{master_id}", 7 * 86400,
                              lambda: discogs.vinyl_versions(master_id))[:top]
    best, total = None, 0
    for v in versions:
        stats = discogs.get_marketplace_stats(v["release_id"])
        total += stats["num_for_sale"] or 0
        if stats["lowest_price"] is not None and (best is None or stats["lowest_price"] < best["lowest_price"]):
            best = dict(stats, release_id=v["release_id"], pressing=pressing_text(v), pressing_count=stats["num_for_sale"])
    if best:
        best.update(num_for_sale=total, checked=len(versions))
    return best


def _ebay_listings(wish: dict, errors: list) -> list:
    try:
        found = ebay.search_listings(wish["artist"], wish["album"], extra=wish.get("catno") or "")
        if wish.get("catno") and len(found) < deals.MIN_COMPARABLES:
            # Few listings mention the catalog number; widen to the album so we have prices to compare.
            more = ebay.search_listings(wish["artist"], wish["album"])
            ids = {l["listing_id"] for l in found}
            found += [l for l in more if l["listing_id"] not in ids]
    except MissingKeyError as exc:
        errors.append({"source": "eBay", "message": str(exc), "setup": True})
        return []
    except ebay.EbayError as exc:
        errors.append({"source": "eBay", "message": str(exc)})
        return []
    found.sort(key=lambda l: deals.total_price(l) or 1e9)
    detailed = []
    for i, listing in enumerate(found):
        if i < DETAIL_LIMIT:
            try:
                listing = ebay.add_details(listing)
            except ebay.EbayError as exc:
                if exc.fatal:
                    errors.append({"source": "eBay", "message": f"Stopped reading listing details: {exc}"})
                    detailed.extend(found[i:])
                    break
        detailed.append(listing)
    return detailed


def _discogs_row(wish: dict, errors: list):
    """Discogs's API gives the cheapest copy and how many are for sale, not each listing."""
    if not (wish.get("release_id") or wish.get("master_id")):
        return None
    try:
        if wish.get("release_id"):
            stats = discogs.get_marketplace_stats(wish["release_id"])
            release_id = wish["release_id"]
            title = f"Cheapest of {stats['num_for_sale']:,} copies of this pressing on Discogs, in any condition"
        else:
            stats = cheapest_vinyl(wish["master_id"])
            if not stats:
                return None
            release_id = stats["release_id"]
            title = (f"Cheapest vinyl copy on Discogs, in any condition: the {stats['pressing']} pressing "
                     f"({stats['pressing_count']} for sale). {stats['num_for_sale']:,} for sale across the "
                     f"{stats['checked']} most common pressings")
    except (discogs.DiscogsError, MissingKeyError) as exc:
        errors.append({"source": "Discogs", "message": str(exc)})
        return None
    if not stats["num_for_sale"] or stats["lowest_price"] is None:
        return None
    return {
        "source": "Discogs",
        "listing_id": f"discogs-{release_id}",
        "title": title,
        "url": discogs.marketplace_url(release_id),
        "image": (wish.get("discogs") or {}).get("thumb") or "",
        "price": stats["lowest_price"],
        "shipping": None,
        "shipping_note": "Plus shipping",
        "currency": stats["currency"] or "USD",
        "auction": False,
        "seller_feedback_pct": None,
        "seller_feedback_score": None,
        "description": "",
        "aspects": {},
    }


def _suggestions(wish: dict):
    """Discogs sale-based prices for a specific pressing, if your account has them turned on."""
    if not wish.get("release_id"):
        return None
    try:
        prices, _ = discogs.get_price_suggestions(wish["release_id"])
        return prices or None
    except (discogs.DiscogsError, MissingKeyError):
        return None  # locked or unavailable: fall back to comparing listings


READING_KEPT_S = 30 * 86400  # a listing's text rarely changes, so Claude reads each one once


def _reading_key(listing: dict) -> str:
    text = f"{listing.get('title')}|{listing.get('description')}|{sorted((listing.get('aspects') or {}).items())}"
    return f"claude:listing:{listing.get('listing_id')}:{hashlib.sha1(text.encode()).hexdigest()[:12]}"


def claude_readings(wish: dict, listings: list, errors: list) -> dict:
    """{listing_id: Claude's reading} for the eBay listings, or {} when Claude is off.

    Readings are saved, so a listing seen before costs nothing. A Claude problem is added to
    `errors` as a note and the search carries on with the rule-based grades.
    """
    if not claude.enabled():
        return {}
    out, unread = {}, []
    for l in listings:
        saved = storage.cache_get(_reading_key(l), READING_KEPT_S)
        if saved is not None:
            out[l["listing_id"]] = saved
        else:
            unread.append(l)
    if unread:
        fresh, problem = claude.read_listings(wish["artist"], wish["album"], unread)
        for l in unread:
            if fresh and l["listing_id"] in fresh:
                out[l["listing_id"]] = fresh[l["listing_id"]]
                storage.cache_set(_reading_key(l), fresh[l["listing_id"]])
        if problem:
            errors.append({"source": "Claude", "note": True,
                           "message": f"{problem} Conditions were read by the built-in rules instead."})
    return out


def search_wish(wish_id: int, use_claude: bool = True) -> dict:
    """Searches everything for one wishlist record, rates the listings and saves them."""
    wish = storage.get_wish(wish_id)
    errors = []
    listings = _ebay_listings(wish, errors)
    readings = claude_readings(wish, listings, errors) if use_claude else {}
    row = _discogs_row(wish, errors)
    if row:
        listings.append(row)
    rated = deals.evaluate(listings, wish["max_price"], wish["min_condition"], _suggestions(wish), readings)
    for l in rated:  # keep saved results small
        l["description"] = (l.get("description") or "")[:400]
        l.pop("aspects", None)
    storage.update_wish(wish_id, results=rated, errors=errors, checked_at=storage.now_iso())
    storage.log_listings(wish_id, rated)
    return storage.get_wish(wish_id)


def search_all(on_progress=None) -> list:
    """Searches every wishlist record. Returns the updated wishes."""
    out = []
    wishes = storage.list_wishes()
    for i, wish in enumerate(wishes, start=1):
        if on_progress:
            on_progress(i, len(wishes), wish)
        out.append(search_wish(wish["id"]))
    return out
