"""Turns Discogs price data into a low / typical / high estimate for one record, and totals.

No API calls here: pure functions, so they're easy to test (tests/test_valuation.py).

How the estimate works:
  typical = Discogs's suggested price for this exact pressing in YOUR record's condition
            (Discogs bases these on the pressing's sales history).
  low     = the suggestion one grade worse.
  high    = the suggestion one grade better.
Grading is subjective, so a buyer might grade your VG+ as a VG, or as an NM. The range shows
what the record is worth across that one-grade disagreement. If Discogs has no data, the value
is "unknown" — we never make one up.
"""

from core import grading

OK = "ok"
UNKNOWN = "unknown"
PRICES_LOCKED_REASON = ("Value unknown: Discogs price data isn't turned on for your account (it needs PayPal), "
                        "and eBay keys aren't set for estimates from eBay listings.")


def locked(currency: str) -> dict:
    """The value we store when Discogs won't share price suggestions with this account."""
    return {"low": None, "typical": None, "high": None, "currency": currency, "status": UNKNOWN,
            "locked": True, "reason": PRICES_LOCKED_REASON}


def estimate(suggestions: dict, condition: str, currency: str) -> dict:
    """suggestions: {grade name: price}. Returns a value dict that's stored on the record."""
    base = {"low": None, "typical": None, "high": None, "currency": currency, "status": UNKNOWN}
    if not condition:
        return dict(base, reason="Set the record's condition to get a value.")
    if condition not in grading.MEDIA_GRADES:
        return dict(base, reason=f"Unknown condition “{condition}”.")
    if not suggestions:
        return dict(base, reason="Discogs has no sales data for this pressing yet, so its value is unknown.")
    typical = suggestions.get(condition)
    if typical is None:
        return dict(base, reason=f"Discogs has no price data for this pressing in {grading.short(condition)}.")
    better, worse = grading.neighbors(condition)
    high = suggestions.get(better, typical) if better else typical
    low = suggestions.get(worse, typical) if worse else typical
    low, high = min(low, typical), max(high, typical)
    return {
        "low": round(low, 2),
        "typical": round(typical, 2),
        "high": round(high, 2),
        "currency": currency,
        "status": OK,
        "reason": f"Discogs's suggested price for this pressing in {grading.short(condition)}; "
                  "the range spans one grade below to one grade above.",
    }


def is_valued(record: dict) -> bool:
    value = record.get("value") or {}
    return value.get("status") == OK and value.get("typical") is not None


def summarize(records: list) -> dict:
    """Totals across valued records. Records with no value are counted, not guessed."""
    valued = [r for r in records if is_valued(r)]
    currencies = sorted({r["value"]["currency"] for r in valued if r["value"].get("currency")})
    return {
        "total_low": round(sum(r["value"]["low"] for r in valued), 2),
        "total_typical": round(sum(r["value"]["typical"] for r in valued), 2),
        "total_high": round(sum(r["value"]["high"] for r in valued), 2),
        "currency": currencies[0] if currencies else None,
        "mixed_currency": len(currencies) > 1,
        "valued": len(valued),
        "unvalued": len(records) - len(valued),
        "count": len(records),
    }


def most_valuable(records: list, n: int = 5) -> list:
    return sorted((r for r in records if is_valued(r)), key=lambda r: -r["value"]["typical"])[:n]


# ---------- Estimate from eBay listings (when Discogs prices are locked) ----------

EBAY_MIN_LISTINGS = 3
DECENT_GRADES = ("Near Mint (NM or M-)", "Very Good Plus (VG+)", "Very Good (VG)")


def _pct(values: list, q: float) -> float:
    pos = (len(values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def estimate_from_listings(listings: list, condition: str, currency: str, basis: str,
                           modern: bool = False) -> dict:
    """Low / typical / high from what comparable eBay copies are listed for (asking prices).

    listings: [{"price": float, "grade": media grade or None}], already filtered to this record.
    Uses copies in your record's condition; if fewer than 3, copies one grade either side.
    If your condition isn't set, uses VG to NM copies (for a modern pressing: new and NM copies,
    since those are mostly sold new). Needs at least 3 copies, else unknown.
    `basis` says which listings were searched, e.g. "same catalog number".
    """
    graded = [l for l in listings if l.get("price") is not None and l.get("grade") in grading.MEDIA_GRADES]
    base = {"low": None, "typical": None, "high": None, "currency": currency, "status": UNKNOWN,
            "source": "ebay", "listings": listings}
    if condition in grading.MEDIA_GRADES:
        group = [l for l in graded if l["grade"] == condition]
        what = f"{grading.short(condition)} copies"
        if len(group) < EBAY_MIN_LISTINGS:
            group = [l for l in graded if abs(grading.rank(l["grade"]) - grading.rank(condition)) <= 1]
            what = f"{grading.short(condition)} copies (and one grade either side)"
    elif modern:
        group = [l for l in graded if l["grade"] in ("Mint (M)", "Near Mint (NM or M-)")]
        what = "new and Near Mint copies (set your record's condition for a closer estimate)"
    else:
        group = [l for l in graded if l["grade"] in DECENT_GRADES]
        what = "VG to NM copies (set your record's condition for a closer estimate)"
    if len(group) < EBAY_MIN_LISTINGS:
        n = len(group)
        return dict(base, reason=f"Not enough comparable eBay listings right now ({n} of the {EBAY_MIN_LISTINGS} "
                                 f"needed, {basis}), so the value is unknown.")
    prices = sorted(l["price"] for l in group)
    return dict(base, status=OK, low=round(_pct(prices, 0.25), 2), typical=round(_pct(prices, 0.5), 2),
                high=round(_pct(prices, 0.75), 2), count=len(group),
                reason=f"Estimate from {len(group)} eBay listings of {what}, {basis}. These are asking prices, "
                       "so actual sales are often a little lower.")
