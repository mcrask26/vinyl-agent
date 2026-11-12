"""Collection actions that combine Discogs + valuation + storage. The UI and scripts call these.

Keeping this out of the page file means the daily script (step 3) can reuse it.
"""

from core import ebay_value, storage, valuation
from core.config import MissingKeyError, get_currency
from tools import discogs, ebay


def ebay_estimate(record: dict) -> dict:
    """Value from current eBay listings, used when Discogs's prices are locked.

    No eBay keys -> stays "unknown" (locked). A fatal eBay problem (offline, bad keys) is raised.
    """
    currency = get_currency()
    try:
        return ebay_value.value_record(record, currency)
    except MissingKeyError:
        return valuation.locked(currency)
    except ebay.EbayError as exc:
        if exc.fatal:
            raise
        return {"low": None, "typical": None, "high": None, "currency": currency, "status": valuation.UNKNOWN,
                "source": "ebay", "reason": f"eBay problem, so no estimate this time: {exc}"}


def fetch_value(release_id: int, condition: str, skip_prices: bool = False, record=None) -> dict:
    """Calls Discogs (up to 2 requests) and returns {suggestions, market, value, prices_locked}.

    If Discogs won't share price suggestions with this account, the value is estimated from
    current eBay listings when eBay keys are set (and `record` is given), else "unknown" (never
    guessed). skip_prices=True skips the Discogs call we already know will be refused.
    """
    prices_locked = skip_prices
    suggestions, sugg_currency = None, None
    if not skip_prices:
        try:
            suggestions, sugg_currency = discogs.get_price_suggestions(release_id)
        except discogs.DiscogsPricesUnavailable:
            prices_locked = True
    market = discogs.get_marketplace_stats(release_id)
    if prices_locked:
        value = ebay_estimate(dict(record, media_condition=condition)) if record else valuation.locked(get_currency())
    else:
        value = valuation.estimate(suggestions, condition, sugg_currency or get_currency())
    return {"suggestions": suggestions, "market": market, "value": value, "prices_locked": prices_locked}


def _save_value(record_id: int, release_id: int, condition: str, priced: dict) -> None:
    fields = dict(market=priced["market"], value=priced["value"], valued_at=storage.now_iso())
    if not priced["prices_locked"]:
        fields["suggestions"] = priced["suggestions"]
    storage.update_record(record_id, **fields)
    storage.log_price(release_id, condition, priced["value"]["typical"], priced["market"]["lowest_price"],
                      priced["market"]["num_for_sale"], priced["value"]["currency"])


def confirm_match(record_id: int, release_id: int, skip_prices: bool = False) -> dict:
    """Links a saved record to a Discogs release, then values it.

    Returns the updated record. If pricing fails, the match is still saved and the error is
    re-raised so the page can say so.
    """
    release = discogs.get_release(release_id)
    storage.update_record(record_id, release_id=release_id, release=release,
                          suggestions=None, value=None, market=None, valued_at=None)
    record = storage.get_record(record_id)
    priced = fetch_value(release_id, record["media_condition"], skip_prices, record)
    _save_value(record_id, release_id, record["media_condition"], priced)
    record = storage.get_record(record_id)
    record["prices_locked"] = priced["prices_locked"]
    return record


def revalue_record(record_id: int, skip_prices: bool = False) -> dict:
    record = storage.get_record(record_id)
    if not record or not record["release_id"]:
        raise ValueError("This record isn't matched to a Discogs release yet.")
    priced = fetch_value(record["release_id"], record["media_condition"], skip_prices, record)
    _save_value(record_id, record["release_id"], record["media_condition"], priced)
    record = storage.get_record(record_id)
    record["prices_locked"] = priced["prices_locked"]
    return record


def change_condition(record_id: int, media_condition: str, sleeve_condition: str, notes: str) -> None:
    """Re-estimates from the saved price suggestions — no API call needed."""
    record = storage.get_record(record_id)
    fields = {"media_condition": media_condition, "sleeve_condition": sleeve_condition, "notes": notes}
    value = record.get("value") or {}
    currency = value.get("currency") or get_currency()
    if record.get("suggestions") is not None:
        fields["value"] = valuation.estimate(record["suggestions"], media_condition, currency)
    elif value.get("source") == "ebay" and value.get("listings") is not None:
        new = valuation.estimate_from_listings(value["listings"], media_condition, currency, value.get("basis", ""))
        new["basis"] = value.get("basis", "")
        fields["value"] = new
    storage.update_record(record_id, **fields)


def refresh_all(on_progress=None) -> dict:
    """Re-prices every matched record, then saves a value snapshot.

    Stops early (no snapshot) if Discogs is unreachable or rejects the token, since every
    other record would fail the same way. Per-record problems are collected and reported.
    """
    records = storage.list_records()
    matched = [r for r in records if r["release_id"]]
    failures = []
    prices_locked = False
    for i, rec in enumerate(matched, start=1):
        if on_progress:
            on_progress(i, len(matched), rec)
        try:
            # Once Discogs says prices are off for this account, don't ask again for every record.
            updated = revalue_record(rec["id"], skip_prices=prices_locked)
            prices_locked = prices_locked or bool(updated.get("prices_locked"))
        except discogs.DiscogsNotFound as exc:
            failures.append((rec, f"Discogs couldn't find this release anymore: {exc}"))
        except (discogs.DiscogsError, ebay.EbayError) as exc:
            if exc.fatal:
                raise
            failures.append((rec, str(exc)))
    summary = valuation.summarize(storage.list_records())
    snapshot_saved = summary["valued"] > 0
    if snapshot_saved:
        storage.save_snapshot(summary)
    return {"summary": summary, "failures": failures, "refreshed": len(matched) - len(failures),
            "unmatched": len(records) - len(matched), "snapshot_saved": snapshot_saved,
            "prices_locked": prices_locked}
