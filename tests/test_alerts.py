"""Alerts: saving Great Deals, no duplicates, dismissed stays dismissed, the daily script."""

from datetime import datetime, timedelta, timezone

from core import alerts, deals, storage
from scripts import check_deals
from tests.test_wishlist import ebay_listing
from tools import discogs, ebay

PRICES = [20, 25, 27, 29, 31, 33]  # totals 25..38; the $25 one is a Great Deal


def fake_market(monkeypatch, prices=PRICES, extra=None):
    def search(*a, **k):
        found = [ebay_listing(i, p) for i, p in enumerate(prices)]
        return found + (extra or [])
    monkeypatch.setattr(ebay, "search_listings", search)
    monkeypatch.setattr(ebay, "add_details", lambda l: l)
    monkeypatch.setattr(discogs, "vinyl_versions", lambda mid: [{"release_id": 9, "country": "US", "year": "1977", "catno": "BSK 3010"}])
    monkeypatch.setattr(discogs, "get_marketplace_stats", lambda rid: {"lowest_price": None, "currency": None, "num_for_sale": 0})


def test_check_now_saves_great_deals_once(monkeypatch):
    fake_market(monkeypatch)
    storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", 40, master_id=1)
    first = alerts.check_now()
    assert first["searched"] == 1 and len(first["new"]) == 1
    a = first["new"][0]
    assert a["total"] == 25.0 and a["url"] == "https://ebay.com/itm/0" and a["status"] == "new"
    assert "Listed at $25.00" in a["reason"]
    assert "1 new Great Deal" in alerts.summary_text(first)
    # Same listing next time: no duplicate alert
    second = alerts.check_now(trigger="daily script")
    assert second["new"] == [] and len(storage.list_alerts()) == 1
    assert storage.count_new_alerts() == 1
    assert storage.last_check_run("daily script")["new_alerts"] == 0


def test_dismissed_never_comes_back_and_sold_is_marked(monkeypatch):
    fake_market(monkeypatch)
    storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", 40, master_id=1)
    a = alerts.check_now()["new"][0]
    storage.set_alert_status(a["id"], "dismissed")
    assert alerts.check_now()["new"] == []
    assert storage.list_alerts("dismissed")[0]["id"] == a["id"]
    # The deal disappears from the next search (sold): kept, but marked not listed
    fake_market(monkeypatch, prices=[30, 31, 32, 33, 34, 35])
    alerts.check_now()
    assert storage.list_alerts()[0]["still_listed"] == 0
    assert storage.count_new_alerts() == 0


def test_over_max_suspicious_and_early_auctions_dont_alert(monkeypatch):
    shady = ebay_listing(90, 19, title="Fleetwood Mac Rumours LP VG+/VG")
    shady["seller_feedback_pct"] = 90.0
    fake_market(monkeypatch, extra=[shady])
    storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", 20, master_id=1)  # max $20: nothing fits
    assert alerts.check_now()["new"] == []

    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    base = dict(rating=deals.GREAT, meets=True, suspicious=False, auction=True)
    assert deals.is_alert_worthy(dict(base, ends=soon))
    assert not deals.is_alert_worthy(dict(base, ends=later))
    assert not deals.is_alert_worthy(dict(base, ends=None))


def test_one_failing_record_doesnt_stop_the_rest(monkeypatch):
    fake_market(monkeypatch)
    storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", 40, master_id=1)
    storage.add_wish("Broken", "Record", "Very Good (VG)", 40)
    real = alerts.wishlist.search_wish

    def flaky(wid):
        if storage.get_wish(wid)["artist"] == "Broken":
            raise RuntimeError("boom")
        return real(wid)
    monkeypatch.setattr(alerts.wishlist, "search_wish", flaky)
    result = alerts.check_now()
    assert len(result["new"]) == 1 and result["errors"][0]["wish"] == "Broken – Record"


def test_daily_script(monkeypatch, capsys):
    fake_market(monkeypatch)
    storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", 40, master_id=1)
    notes = []
    monkeypatch.setattr(check_deals, "notify", lambda title, text: notes.append(text))
    assert check_deals.main([]) == 0
    out = capsys.readouterr().out
    assert "1 new Great Deal" in out and "https://ebay.com/itm/0" in out
    assert notes and "Rumours" in notes[0]
    assert check_deals.main(["--no-notify"]) == 0 and len(notes) == 1


def test_deleting_a_wish_removes_its_alerts(monkeypatch):
    fake_market(monkeypatch)
    wid = storage.add_wish("Fleetwood Mac", "Rumours", "Very Good (VG)", 40, master_id=1)
    alerts.check_now()
    storage.delete_wish(wid)
    assert storage.list_alerts() == []
