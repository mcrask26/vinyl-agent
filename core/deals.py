"""Deal scoring: rates each listing Great Deal / Fair / Overpriced and flags suspicious ones.

Pure logic, no API calls (tested in tests/test_deals.py).

What a listing is compared against (its "reference price"), best source first:
  1. Discogs sale-based price suggestions for the listing's grade (when your Discogs account
     has price data turned on). Phrase: "usually sells for $25 to $30".
  2. Otherwise, what OTHER comparable listings are asking right now (middle half of their
     prices, needs at least 5). Phrase: "similar copies are listed for $25 to $30".
  3. Not enough data -> "Not rated". We never invent a typical price.
"""

import re
import statistics
from datetime import datetime, timedelta, timezone

from core import grading
from core.format import money, money_range

GREAT, FAIR, OVER, UNRATED = "Great Deal", "Fair", "Overpriced", "Not rated"
MIN_COMPARABLES = 5

GREAT_BELOW = 0.85    # at most 85% of typical (and at/below the low end) = Great Deal
OVER_ABOVE = 1.15     # more than 115% of typical (and above the high end) = Overpriced
TOO_CHEAP = 0.40      # under 40% of typical = suspicious
MIN_FEEDBACK_PCT = 97.0
MIN_FEEDBACK_SCORE = 10

NOT_OFFICIAL = re.compile(r"\b(bootleg|unofficial|counterfeit|reproduction|repro|fan[\s-]made)\b", re.I)


def total_price(listing: dict):
    """Price + shipping. Shipping is unknown when not listed; then we use the price alone."""
    if listing.get("price") is None:
        return None
    return round(listing["price"] + (listing.get("shipping") or 0), 2)


def _percentile(sorted_values, q):
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def reference_from_listings(totals: list):
    """Low / typical / high from other listings' totals (middle half), or None if too few."""
    values = sorted(t for t in totals if t is not None)
    if len(values) < MIN_COMPARABLES:
        return None
    return {"low": round(_percentile(values, 0.25), 2), "typical": round(statistics.median(values), 2),
            "high": round(_percentile(values, 0.75), 2), "source": "listings", "count": len(values),
            "phrase": "similar copies are listed for"}


def reference_from_discogs(suggestions: dict, grade: str):
    """Low / typical / high for this grade from Discogs sale-based suggestions, or None."""
    if not suggestions or not grade or grade not in suggestions:
        return None
    from core.valuation import estimate  # local import keeps this module light
    v = estimate(suggestions, grade, "USD")
    if v["typical"] is None:
        return None
    # Tighter band than the collection range: ±10% around the typical price for that grade.
    return {"low": round(v["typical"] * 0.9, 2), "typical": v["typical"], "high": round(v["typical"] * 1.1, 2),
            "source": "discogs", "count": None, "phrase": f"{grading.short(grade)} copies usually sell for"}


def rate(total, ref, currency="USD", shipping_known=True):
    """(rating, one-sentence reason)."""
    if total is None:
        return UNRATED, "No price shown."
    listed = f"Listed at {money(total, currency)}" + (" with shipping" if shipping_known else " plus shipping (not listed)")
    if not ref:
        return UNRATED, f"{listed}; not enough comparable listings or sales data to judge the price."
    typical = ref["typical"]
    if total <= ref["low"] and total <= typical * GREAT_BELOW:
        rating = GREAT
    elif total > ref["high"] and total > typical * OVER_ABOVE:
        rating = OVER
    else:
        rating = FAIR
    return rating, f"{listed}; {ref['phrase']} {money_range(ref['low'], ref['high'], currency)}."


def red_flags(listing: dict, total, ref, grade) -> list:
    """Reasons a listing looks suspicious. Empty list = nothing odd spotted."""
    flags = []
    if ref and total is not None and total < ref["typical"] * TOO_CHEAP:
        flags.append(f"Price is far below normal ({money(total)} vs about {money(ref['typical'])}). "
                     "Could be a mistake, a different pressing, or a scam.")
    pct, score = listing.get("seller_feedback_pct"), listing.get("seller_feedback_score")
    if pct is not None and pct < MIN_FEEDBACK_PCT:
        flags.append(f"Seller has only {pct:g}% positive feedback.")
    if score is not None and score < MIN_FEEDBACK_SCORE:
        flags.append(f"New seller with only {score} rating{'s' if score != 1 else ''}.")
    if grade and grading.at_least(grade, "Very Good Plus (VG+)"):
        damage = grading.damage_mentions(f"{listing.get('title', '')} {listing.get('description', '')}")
        if damage:
            flags.append(f"Graded {grading.short(grade)}, but the description mentions “{', '.join(damage[:3])}”.")
    m = NOT_OFFICIAL.search(listing.get("title", ""))
    if m:
        flags.append(f"Title says “{m.group(0)}”: may not be an official pressing.")
    return flags


def evaluate(listings: list, max_price, min_condition: str, suggestions=None) -> list:
    """Adds total, grade, rating, reason, flags and whether it fits your limits to each listing.

    Each listing is compared with the OTHER listings (leave-one-out), so a listing can't make
    itself look normal. Discogs-only rows (no grade) are compared against all eBay listings.
    """
    for l in listings:
        cond = grading.parse_listing_condition(l.get("title", ""), l.get("description", ""), l.get("aspects"),
                                               l.get("ebay_condition", ""))
        if l.get("grade_override"):
            cond = {"media": l["grade_override"], "sleeve": None, "evidence": "from the marketplace"}
        l.update(total=total_price(l), grade=cond["media"], sleeve_grade=cond["sleeve"], grade_evidence=cond["evidence"])

    comparable = [l for l in listings if l.get("source") == "eBay" and l["total"] is not None]
    out = []
    for l in listings:
        ref = reference_from_discogs(suggestions, l["grade"]) if suggestions else None
        if not ref:
            others = [o["total"] for o in comparable if o is not l]
            ref = reference_from_listings(others)
        rating, reason = rate(l["total"], ref, l.get("currency") or "USD", shipping_known=l.get("shipping") is not None)
        flags = red_flags(l, l["total"], ref, l["grade"])
        meets_price = max_price is None or (l["total"] is not None and l["total"] <= max_price)
        meets_condition = bool(l["grade"]) and grading.at_least(l["grade"], min_condition)
        out.append(dict(l, rating=rating, reason=reason, flags=flags, suspicious=bool(flags),
                        reference=ref, meets_price=meets_price, meets_condition=meets_condition,
                        meets=meets_price and meets_condition))
    order = {GREAT: 0, FAIR: 1, UNRATED: 2, OVER: 3}
    return sorted(out, key=lambda x: (not x["meets"], x["suspicious"], order[x["rating"]], x["total"] or 1e9))


AUCTION_ALERT_HOURS = 24


def auction_ends_soon(listing: dict, hours: int = AUCTION_ALERT_HOURS) -> bool:
    """True for an auction ending within `hours`. Early bids are always low, so earlier ones don't count."""
    try:
        ends = datetime.fromisoformat((listing.get("ends") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) <= ends <= datetime.now(timezone.utc) + timedelta(hours=hours)


def is_alert_worthy(listing: dict) -> bool:
    """A Great Deal that fits your price and condition and doesn't look suspicious.

    Auctions only count in their last 24 hours: a low bid days before the end says little.
    """
    if listing.get("auction") and not auction_ends_soon(listing):
        return False
    return listing["rating"] == GREAT and listing["meets"] and not listing["suspicious"]


def recheck_limits(listings: list, max_price, min_condition: str) -> list:
    """Re-applies your max price / minimum condition to saved results (no new searching)."""
    out = []
    for l in listings:
        meets_price = max_price is None or (l.get("total") is not None and l["total"] <= max_price)
        meets_condition = bool(l.get("grade")) and grading.at_least(l["grade"], min_condition)
        out.append(dict(l, meets_price=meets_price, meets_condition=meets_condition,
                        meets=meets_price and meets_condition))
    return out
