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
        "low_confidence": sum(1 for r in valued if r["value"].get("confidence") == LOW),
        "count": len(records),
    }


def most_valuable(records: list, n: int = 5) -> list:
    return sorted((r for r in records if is_valued(r)), key=lambda r: -r["value"]["typical"])[:n]


# ---------- Estimate from eBay listings (when Discogs prices are locked) ----------

EBAY_MIN_LISTINGS = 3
OUTLIER_TIMES = 4          # a listing over 4x the middle price of ALL copies of the record is ignored
OUTLIER_MIN_LISTINGS = 5   # ...but only when there are enough copies to know what "the middle" is
LOW, MEDIUM, GOOD = "low", "medium", "good"
CONFIDENCE_TEXT = {LOW: "Low confidence", MEDIUM: "Medium confidence", GOOD: "Good confidence"}
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
    group, dropped = _without_outliers(group, listings)
    ignored = (f" {dropped} listing{'s' if dropped != 1 else ''} priced far above the rest "
               f"{'were' if dropped != 1 else 'was'} ignored.") if dropped else ""
    if len(group) < EBAY_MIN_LISTINGS:
        n = len(group)
        return dict(base, reason=f"Not enough comparable eBay listings right now ({n} of the {EBAY_MIN_LISTINGS} "
                                 f"needed, {basis}), so the value is unknown.{ignored}")
    prices = sorted(l["price"] for l in group)
    level = confidence(len(group), basis)
    return dict(base, status=OK, low=round(_pct(prices, 0.25), 2), typical=round(_pct(prices, 0.5), 2),
                high=round(_pct(prices, 0.75), 2), count=len(group), confidence=level, ignored=dropped,
                reason=f"Estimate from {len(group)} eBay listings of {what}, {basis}. These are asking prices, "
                       f"so actual sales are often a little lower.{ignored}")


def _without_outliers(group: list, all_listings: list):
    """(group without listings priced far above the rest, how many were dropped).

    "The rest" is every copy of the record on sale, whatever its grade, so a seller asking $80
    for a record that otherwise goes for $11-$20 doesn't set the value.
    """
    prices = sorted(l["price"] for l in all_listings if l.get("price") is not None)
    if len(prices) < OUTLIER_MIN_LISTINGS:
        return group, 0
    limit = _pct(prices, 0.5) * OUTLIER_TIMES
    kept = [l for l in group if l["price"] <= limit]
    return kept, len(group) - len(kept)


def confidence(count: int, basis: str) -> str:
    """How far to trust an eBay estimate: more listings, and the same pressing, mean more trust."""
    level = GOOD if count >= 10 else MEDIUM if count >= 5 else LOW
    if "any pressing" in (basis or "") and level != LOW:
        level = MEDIUM if level == GOOD else LOW  # other pressings can be worth more or less than yours
    return level


def confidence_note(value: dict) -> str:
    """Short reason for a low/medium rating, for the screen. Empty for good or non-eBay values."""
    level = value.get("confidence")
    if level not in (LOW, MEDIUM):
        return ""
    why = []
    if (value.get("count") or 0) < 10:
        why.append(f"only {value.get('count')} comparable listings")
    if "any pressing" in (value.get("basis") or value.get("reason") or ""):
        why.append("other pressings are mixed in")
    return f"{CONFIDENCE_TEXT[level]}: {' and '.join(why)}." if why else CONFIDENCE_TEXT[level] + "."
