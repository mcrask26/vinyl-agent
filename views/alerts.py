"""Alerts: Great Deals found on your wishlist, each with a link to buy it yourself."""

from pathlib import Path

import streamlit as st

from core import alerts, deals, grading, storage
from core.format import ago, money
from core.ui import flash, md, show_flash

ROOT = Path(__file__).resolve().parent.parent
SCHEDULE_PLIST = Path.home() / "Library/LaunchAgents/com.vinylagent.dailycheck.plist"


def grade_text(a: dict) -> str:
    if not a.get("grade"):
        return "condition unknown"
    g = grading.short(a["grade"])
    return g + (f" / {grading.short(a['sleeve_grade'])}" if a.get("sleeve_grade") else "")


def alert_card(a: dict) -> None:
    with st.container(border=True):
        img, body, act = st.columns([1, 4, 1.4], vertical_alignment="center")
        if a.get("image"):
            img.image(a["image"], width="stretch")
        body.markdown(md(f"**{a['artist']} – {a['album']}** · **{money(a['total'], a['currency'])}** "
                         f"on {a['source']} · {grade_text(a)}"))
        body.caption(md(a.get("reason") or ""))
        bits = [f"Found {ago(a['found_at'])}", a.get("title") or ""]
        if a.get("auction"):
            bits.insert(1, "Auction: the price can still go up")
        if not a["still_listed"]:
            bits.insert(1, "⚠️ No longer a Great Deal in the latest check: it may have sold or the price changed")
        body.caption(md(" · ".join(b for b in bits if b)))
        act.link_button(f"Buy now on {a['source']}", a["url"], type="primary", icon=":material/open_in_new:",
                        width="stretch", help="Opens the listing. You check it and pay there yourself.")
        if a["status"] == "new" and act.button("Mark as seen", key=f"seen_{a['id']}", width="stretch"):
            storage.set_alert_status(a["id"], "seen")
            st.rerun()
        if a["status"] != "dismissed" and act.button("Dismiss", key=f"dis_{a['id']}", width="stretch",
                                                     help="Hide it. This listing won't alert you again."):
            storage.set_alert_status(a["id"], "dismissed")
            st.rerun()
        if a["status"] == "dismissed" and act.button("Restore", key=f"res_{a['id']}", width="stretch"):
            storage.set_alert_status(a["id"], "seen")
            st.rerun()


# ---------- Page ----------

st.title("Alerts")
st.caption("Great Deals on your wishlist: a price that fits your max, a condition that meets your minimum, "
           "and nothing suspicious about the listing.")
st.info("Vinyl Agent never buys anything. **Buy now** only opens the listing on eBay or Discogs, "
        "where you check it and pay yourself.", icon=":material/shield:")
show_flash()

wishes = storage.list_wishes()
last = storage.last_check_run()
last_daily = storage.last_check_run("daily script")

c1, c2, c3 = st.columns([2, 2, 1.4], vertical_alignment="bottom")
c1.metric("New alerts", storage.count_new_alerts())
c2.metric("Last check", ago(last["ran_at"]) if last else "never",
          help=f"By {last['trigger']}. Daily script last ran {ago(last_daily['ran_at']) if last_daily else 'never'}."
          if last else None)
if c3.button("Check now", type="primary", icon=":material/search:", disabled=not wishes, width="stretch",
             help="Searches every wishlist record now (about 15 eBay calls each)."):
    bar = st.progress(0.0, text="Checking your wishlist…")

    def progress(i, n, w):
        bar.progress((i - 1) / n, text=f"Checking {i} of {n}: {w['artist']} – {w['album']}")

    result = alerts.check_now(trigger="button", on_progress=progress)
    bar.empty()
    flash("success" if not result["errors"] else "warning", alerts.summary_text(result))
    for e in result["errors"]:
        flash("warning", f"{e['wish']} ({e['source']}): {e['message']}")
    st.rerun()

if not wishes:
    st.warning("Your wishlist is empty. Add records on the Wishlist & Deals page, then check for deals here.")

if last and last["errors"]:
    with st.expander(f"{len(last['errors'])} problem(s) in the last check", icon=":material/error:"):
        for e in last["errors"]:
            st.caption(md(f"**{e['wish']}** ({e['source']}): {e['message']}"))

with st.expander("Check automatically every day", icon=":material/schedule:"):
    if SCHEDULE_PLIST.exists():
        st.success("The daily check is **on**.")
    else:
        st.write("The daily check is **off**.")
    st.markdown("Paste one of these into **Terminal**. If your Mac is asleep at that time, it runs when it wakes up, "
                "and you get a notification when it finds new Great Deals.")
    st.code(f'cd "{ROOT}"\nbash scripts/schedule_daily_check.sh on 9:00', language="bash")
    st.caption("Turn it off:")
    st.code(f'cd "{ROOT}"\nbash scripts/schedule_daily_check.sh off', language="bash")
    st.caption("Run one check by hand: `.venv/bin/python scripts/check_deals.py`. "
               "Results of daily runs are logged in data/daily_check.log. Auctions only alert in their last "
               f"{deals.AUCTION_ALERT_HOURS} hours, since early bids are always low.")

st.divider()

all_alerts = storage.list_alerts()
groups = {s: [a for a in all_alerts if a["status"] == s] for s in ("new", "seen", "dismissed")}
tabs = st.tabs([f"New ({len(groups['new'])})", f"Seen ({len(groups['seen'])})",
                f"Dismissed ({len(groups['dismissed'])})"])
empty = {"new": "No new alerts. Click **Check now**, or turn on the daily check.",
         "seen": "Alerts you mark as seen show up here.",
         "dismissed": "Dismissed alerts show up here. A dismissed listing never alerts you again."}
for tab, status in zip(tabs, groups):
    with tab:
        items = groups[status]  # newest first (from storage)
        items = [a for a in items if a["still_listed"]] + [a for a in items if not a["still_listed"]]
        if not items:
            st.caption(empty[status])
        if status == "new" and len(items) > 1 and st.button("Mark all as seen"):
            for a in items:
                storage.set_alert_status(a["id"], "seen")
            st.rerun()
        for a in items:
            alert_card(a)
