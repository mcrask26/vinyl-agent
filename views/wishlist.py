"""Wishlist & Deal Finder: records you want, current listings for each, and how good each price is."""

import pandas as pd
import streamlit as st

from core import deals, grading, storage, wishlist
from core.config import MissingKeyError, get_discogs_token, get_ebay_credentials
from core.format import ago, money
from core.ui import claude_status, flash, md, show_flash
from tools import discogs

RATING_LABEL = {deals.GREAT: "🟢 Great Deal", deals.FAIR: "🟡 Fair", deals.OVER: "🔴 Overpriced",
                deals.UNRATED: "⚪ Not rated"}


def key_status():
    status = {}
    for name, check in (("discogs", get_discogs_token), ("ebay", get_ebay_credentials)):
        try:
            check()
            status[name] = None
        except MissingKeyError as exc:
            status[name] = str(exc)
    return status


@st.cache_data(ttl=3600, show_spinner=False)
def discogs_candidates(artist, album, catno):
    if catno:
        cands, note = discogs.find_release_candidates(artist, album, catno, limit=6)
        return cands, note
    return discogs.find_master_candidates(artist, album, limit=6), None


def fits_text(l: dict, wish: dict) -> str:
    if l["source"] == "Discogs":
        return "Check condition on Discogs"
    if l["meets"]:
        return "✓ Fits"
    parts = []
    if not l["meets_price"]:
        parts.append("Over your max" if l.get("total") is not None else "No price")
    if not l["meets_condition"]:
        parts.append("Condition unknown" if not l.get("grade") else f"Below {grading.short(wish['min_condition'])}")
    return " · ".join(parts)


def seller_text(l: dict) -> str:
    pct, score = l.get("seller_feedback_pct"), l.get("seller_feedback_score")
    if pct is None and score is None:
        return ""
    return " · ".join(x for x in [f"{pct:g}%" if pct is not None else "", f"{score:,} ratings" if score is not None else ""] if x)


def grade_text(l: dict) -> str:
    if not l.get("grade"):
        return "Unknown"
    g = grading.short(l["grade"])
    if l.get("sleeve_grade"):
        g += f" / {grading.short(l['sleeve_grade'])}"
    return g


def run_search(wish_ids: list):
    bar = st.progress(0.0, text="Searching…")
    for i, wid in enumerate(wish_ids, start=1):
        w = storage.get_wish(wid)
        bar.progress((i - 1) / len(wish_ids), text=f"Searching {i} of {len(wish_ids)}: {w['artist']} – {w['album']}")
        wishlist.search_wish(wid)
    bar.empty()


# ---------- Page ----------

st.title("Wishlist & Deals")
st.caption("Records you want, with current listings from eBay and Discogs. Each listing's total price (with shipping) "
           "is compared with what that record normally goes for, and rated Great Deal, Fair or Overpriced.")

keys = key_status()
if keys["discogs"]:
    st.warning(keys["discogs"], icon=":material/key:")
if keys["ebay"]:
    st.info(keys["ebay"], icon=":material/storefront:")
show_flash()
claude_status()

wishes = storage.list_wishes()

top1, top2 = st.columns([3, 1], vertical_alignment="bottom")
top1.caption(f"{len(wishes)} record(s) on your wishlist. Searching costs about 15 eBay calls and 2 Discogs calls "
             "per record (eBay allows 5,000 a day).")
if top2.button("Search all", icon=":material/search:", type="primary", disabled=not wishes, width="stretch"):
    run_search([w["id"] for w in wishes])
    flash("success", f"Searched {len(wishes)} record(s).")
    st.rerun()

# ----- Add to wishlist -----

adding = st.session_state.get("wish_adding")

if not adding:
    with st.expander("Add a record to your wishlist", icon=":material/add:", expanded=not wishes):
        with st.form("add_wish", border=False):
            a1, a2, a3 = st.columns(3)
            artist = a1.text_input("Artist *", placeholder="Fleetwood Mac")
            album = a2.text_input("Album *", placeholder="Rumours")
            catno = a3.text_input("Catalog number (only this pressing)", placeholder="Optional, e.g. BSK 3010",
                                  help="Leave blank to accept any vinyl pressing of the album.")
            b1, b2, b3 = st.columns(3)
            max_price = b1.number_input("Max price incl. shipping ($)", min_value=0.0, value=None, step=5.0,
                                        placeholder="No limit")
            min_cond = b2.selectbox("Minimum condition you'll accept", grading.MEDIA_GRADES, index=2,
                                    help=grading.GRADE_HELP)
            notes = b3.text_input("Notes", placeholder="Want an original, not a reissue")
            go = st.form_submit_button("Find on Discogs", icon=":material/search:", disabled=bool(keys["discogs"]))
        if go:
            if not artist.strip() or not album.strip():
                st.error("Please fill in the artist and album.")
            else:
                try:
                    cands, note = discogs_candidates(artist.strip(), album.strip(), catno.strip())
                except discogs.DiscogsError as exc:
                    st.error(f"Couldn't search Discogs: {exc}")
                else:
                    st.session_state["wish_adding"] = dict(artist=artist.strip(), album=album.strip(),
                                                           catno=catno.strip(), max_price=max_price,
                                                           min_condition=min_cond, notes=notes,
                                                           candidates=cands, note=note)
                    st.rerun()
else:
    def save_wish(cand):
        a = st.session_state.pop("wish_adding")
        info = None
        if cand:
            info = {k: cand.get(k) for k in ("artist", "album", "year", "thumb", "cover", "url", "catno", "country",
                                             "labels", "genres", "styles")}
        wid = storage.add_wish(a["artist"], a["album"], a["min_condition"], a["max_price"], a["catno"],
                               master_id=cand.get("master_id") if cand else None,
                               release_id=cand.get("release_id") if cand and cand["kind"] == "release" else None,
                               discogs=info, notes=a["notes"])
        with st.spinner("Searching listings…"):
            wishlist.search_wish(wid)
        flash("success", f"Added “{a['artist']} – {a['album']}” to your wishlist and searched for listings.")
        st.rerun()

    with st.container(border=True):
        what = "pressing" if adding["catno"] else "album"
        st.subheader(f"Which {what}? {adding['artist']} – {adding['album']}")
        st.caption("Linking it to Discogs adds Discogs's cheapest copy and a cover. "
                   + ("Only this exact pressing will count." if adding["catno"] else "Any vinyl pressing counts."))
        if adding["note"]:
            st.info(adding["note"])
        cands = adding["candidates"]
        if not cands:
            st.warning("Discogs found nothing for this. You can still add it: eBay will be searched by artist and album.")
        cols = st.columns(3)
        for i, c in enumerate(cands):
            with cols[i % 3].container(border=True):
                img, info = st.columns([1, 2])
                if c["thumb"]:
                    img.image(c["thumb"], width="stretch")
                info.markdown(f"**{c['artist']} – {c['album']}**")
                info.caption(" · ".join(str(x) for x in [c["year"] or "", c["country"], c["catno"]] if x))
                info.caption(f"{c['have']:,} people own this · [Discogs]({c['url']})")
                if st.button("This one", key=f"wpick_{i}", width="stretch"):
                    save_wish(c)
        b1, b2, _ = st.columns([1, 1, 2])
        if b1.button("Add without Discogs"):
            save_wish(None)
        if b2.button("Cancel"):
            st.session_state.pop("wish_adding")
            st.rerun()

st.divider()

# ----- The wishlist -----

if not wishes:
    st.info("Your wishlist is empty. Add a record above.")
    st.stop()

only_fits = st.toggle("Only show listings within my max price and condition", value=True)
setup_errors_shown = set()

for w in wishes:
    results = deals.recheck_limits(w["results"] or [], w["max_price"], w["min_condition"])
    great = sum(1 for l in results if deals.is_alert_worthy(l))
    info = w.get("discogs") or {}
    label = f"**{w['artist']} – {w['album']}**"
    if w["checked_at"] is None:
        label += " · not searched yet"
    elif great:
        label += f" · 🟢 {great} Great Deal{'s' if great != 1 else ''}"
    else:
        label += f" · {len(results)} listing{'s' if len(results) != 1 else ''}, no great deals"

    with st.expander(md(label), expanded=great > 0 or len(wishes) <= 3):
        h1, h2, h3, h4 = st.columns([0.6, 3, 1, 1], vertical_alignment="center")
        if info.get("thumb"):
            h1.image(info["thumb"], width="stretch")
        limits = [f"max {money(w['max_price'])}" if w["max_price"] is not None else "no max price",
                  f"min condition {grading.short(w['min_condition'])}",
                  f"only pressing {w['catno']}" if w["catno"] else "any vinyl pressing"]
        h2.markdown(md(" · ".join(limits)))
        h2.caption(f"Last searched {ago(w['checked_at'])}"
                   + (f" · [Discogs page]({info['url']})" if info.get("url") else " · not linked to Discogs")
                   + (f" · {w['notes']}" if w["notes"] else ""))
        if h3.button("Search now", key=f"search_{w['id']}", width="stretch", icon=":material/refresh:"):
            run_search([w["id"]])
            flash("success", f"Searched “{w['artist']} – {w['album']}”.")
            st.rerun()
        with h4.popover("Edit", width="stretch"):
            with st.form(f"edit_wish_{w['id']}", border=False):
                new_max = st.number_input("Max price incl. shipping ($)", min_value=0.0, value=w["max_price"],
                                          step=5.0, placeholder="No limit")
                new_min = st.selectbox("Minimum condition", grading.MEDIA_GRADES,
                                       index=grading.MEDIA_GRADES.index(w["min_condition"]))
                if st.form_submit_button("Save"):
                    storage.update_wish(w["id"], max_price=new_max, min_condition=new_min)
                    flash("success", "Saved. Listings were re-checked against your new limits.")
                    st.rerun()
            if st.button("Remove from wishlist", key=f"del_wish_{w['id']}", type="primary"):
                storage.delete_wish(w["id"])
                flash("info", f"Removed “{w['artist']} – {w['album']}”.")
                st.rerun()

        for err in w.get("errors") or []:
            if err.get("setup"):
                setup_errors_shown.add(err["source"])  # already explained at the top of the page
                continue
            (st.caption if err.get("note") else st.warning)(md(f"{err['source']}: {err['message']}"))

        if w["checked_at"] is None:
            st.caption("Click “Search now” to find listings.")
            continue
        if not results:
            st.info("No current listings found for this record.")
            continue

        # The Discogs row is a summary (cheapest copy, any condition), so the toggle never hides it.
        shown = [l for l in results if l["meets"] or l["source"] == "Discogs"] if only_fits else results
        if not any(l["source"] == "eBay" for l in results) and keys["ebay"]:
            st.info(md("Right now only **Discogs** is searched, and its API only gives the cheapest copy "
                       "(in any condition) and how many are for sale, not each listing. Click **Open listing** "
                       "to browse them all on Discogs. To get individual listings with Great Deal / Fair / "
                       "Overpriced ratings, add free eBay keys (see the note at the top of the page)."),
                    icon=":material/info:")
        counts = {r: sum(1 for l in results if l["rating"] == r) for r in RATING_LABEL}
        st.caption(md(" · ".join(f"{RATING_LABEL[r]}: {n}" for r, n in counts.items() if n)
                      + f" · {sum(1 for l in results if l['suspicious'])} with warnings"))
        if only_fits and not shown:  # (only possible when there's no Discogs row)
            st.info(f"None of the {len(results)} listings fit your max price and condition. "
                    "Turn off the toggle above to see them all.")
            continue

        df = pd.DataFrame([{
            "Image": l.get("image") or None,
            "Rating": RATING_LABEL[l["rating"]],
            "Total": l.get("total"),
            "Why": l["reason"],
            "Warnings": "⚠️ " + " ".join(l["flags"]) if l["flags"] else "",
            "Condition": grade_text(l),
            "Fits you": fits_text(l, w),
            "Source": l["source"] + (" (auction)" if l.get("auction") else ""),
            "Shipping": money(l["shipping"]) if l.get("shipping") is not None else (l.get("shipping_note") or ""),
            "Seller": seller_text(l),
            "Listing": l["title"],
            "Open": l["url"],
        } for l in shown])
        st.dataframe(
            df, hide_index=True, row_height=56,
            column_config={
                "Image": st.column_config.ImageColumn(width="small"),
                "Total": st.column_config.NumberColumn("Total", format="$%.2f", help="Price plus shipping"),
                "Why": st.column_config.TextColumn(width="large"),
                "Warnings": st.column_config.TextColumn(width="medium"),
                "Condition": st.column_config.TextColumn(help="Record / sleeve grade, read from the listing"),
                "Listing": st.column_config.TextColumn(width="medium"),
                "Open": st.column_config.LinkColumn(display_text="Open listing"),
            },
        )
        with st.popover("How the condition was read"):
            for l in shown:
                by = " (read by Claude)" if l.get("read_by") == "Claude" else ""
                st.caption(md(f"**{grade_text(l)}**{by} ← {l.get('grade_evidence') or 'nothing in the listing'} · {l['title'][:70]}"))
