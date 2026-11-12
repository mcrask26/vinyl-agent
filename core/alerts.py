"""Alerts: searches every wishlist record and saves new Great Deals as alerts.

Used by the "Check now" button on the Alerts page and by scripts/check_deals.py (the daily run).
This only finds and saves deals. Nothing here (or anywhere in the app) buys anything:
each alert links to the listing, and you decide and pay there yourself.
"""

from core import deals, storage, wishlist


def check_now(trigger: str = "button", on_progress=None) -> dict:
    """Searches all wishlist records, saves new alert-worthy deals and logs the run.

    Returns {"searched", "new" (list of new alerts), "errors" (list of {wish, source, message})}.
    One record failing doesn't stop the others.
    """
    wishes = storage.list_wishes()
    new, errors = [], []
    for i, wish in enumerate(wishes, start=1):
        name = f"{wish['artist']} – {wish['album']}"
        if on_progress:
            on_progress(i, len(wishes), wish)
        try:
            wish = wishlist.search_wish(wish["id"])
        except Exception as exc:  # report it and keep going with the next record
            errors.append({"wish": name, "source": "Search", "message": f"Search failed: {exc}"})
            continue
        for err in wish.get("errors") or []:
            if not err.get("setup"):
                errors.append({"wish": name, "source": err["source"], "message": err["message"]})
        worthy = [l for l in wish["results"] or [] if deals.is_alert_worthy(l)]
        new += storage.save_alerts(wish, worthy)
    storage.log_check_run(trigger, len(wishes), len(new), errors)
    return {"searched": len(wishes), "new": new, "errors": errors}


def summary_text(result: dict) -> str:
    """One line like 'Checked 4 records: 2 new Great Deals.'"""
    n, s = len(result["new"]), result["searched"]
    if not s:
        return "Your wishlist is empty, so there was nothing to check."
    deals_txt = f"{n} new Great Deal{'s' if n != 1 else ''}" if n else "no new Great Deals"
    text = f"Checked {s} record{'s' if s != 1 else ''}: {deals_txt}."
    if result["errors"]:
        text += f" {len(result['errors'])} problem(s) during the search."
    return text
