"""My Collection: add records, match each to its exact Discogs pressing, and track their value."""

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from core import collection, grading, storage, valuation
from core.config import MissingKeyError, get_discogs_token
from core.format import ago, condition, money, money_range, pressing
from core.ui import flash, md, show_flash
from tools import discogs, ebay

ACCENT = "#B4441C"  # matches primaryColor in .streamlit/config.toml


# ---------- Small helpers ----------

def for_sale(market: dict, currency: str) -> str:
    """'3 · from $15.00' — what's listed on Discogs right now (any condition)."""
    if not market:
        return ""
    n = market.get("num_for_sale") or 0
    lowest = market.get("lowest_price")
    if not n:
        return "None listed"
    return (f"{n} for sale · cheapest {money(lowest, market.get('currency') or currency)} (any condition)"
            if lowest is not None else f"{n} for sale")


def discogs_ready():
    try:
        get_discogs_token()
        return True, None
    except MissingKeyError as exc:
        return False, str(exc)


@st.cache_data(ttl=3600, show_spinner=False)
def search_discogs(artist, album, catno):
    return discogs.find_release_candidates(artist, album, catno)


def title_of(rec: dict) -> str:
    rel = rec.get("release") or {}
    year = rel.get("year")
    return f"{rec['artist']} – {rec['album']}" + (f" ({year})" if year else "")


def value_note(rec: dict) -> str:
    if not rec["release_id"]:
        return "Not matched to Discogs yet, so no value."
    value = rec.get("value")
    if not value:
        return "Value not fetched yet. Click “Refresh values”."
    if value.get("locked"):
        return "Unknown: Discogs price data is off for your account, and eBay keys aren't set."
    return value.get("reason", "") if value.get("status") != valuation.OK else ""


# ---------- Page ----------

st.title("My Collection")
st.caption("Each record is matched to its exact pressing on Discogs. Values come from Discogs's sale-based prices when your account has them, otherwise from current eBay listings.")

ready, key_problem = discogs_ready()
if not ready:
    st.warning(key_problem, icon=":material/key:")

show_flash()
records = storage.list_records()
summary = valuation.summarize(records)
cur = summary["currency"] or "USD"
prices_locked = any((r.get("value") or {}).get("locked") for r in records)
ebay_valued = any((r.get("value") or {}).get("source") == "ebay" for r in records)
if ebay_valued:
    st.info("**Values are estimates from eBay listings.** Discogs's sale-based prices need PayPal, so each "
            "record's value comes from what comparable copies are listed for on eBay right now, in the same "
            "condition as yours (or VG to NM if you haven't set it). These are asking prices, so real sales "
            "are often a bit lower. Records with fewer than 3 comparable listings show as unknown.",
            icon=":material/storefront:")
elif prices_locked:
    st.info("**Values are Unknown for now.** Discogs only shares sale-based prices with accounts that "
            "finished seller setup, including connecting PayPal "
            f"([seller settings]({discogs.SELLER_SETTINGS_URL})). Without that, the app can estimate values "
            "from eBay listings once eBay keys are set (see README.md), then click “Refresh values”.\n\n"
            "Until then, the table only shows the **cheapest copy listed for sale, in any condition**. "
            "That's usually a worn or damaged copy, so it is **not** what your record is worth: "
            "a 25¢ listing doesn't mean a nice copy sells for 25¢.", icon=":material/lock:")

# ----- Summary + refresh -----

last_valued = max((r["valued_at"] for r in records if r["valued_at"]), default=None)
c1, c2, c3, c4 = st.columns([1.3, 1, 1, 1])
c1.metric("Total collection value", money(summary["total_typical"], cur) if summary["valued"] else "Unknown",
          help="Sum of the typical value of every valued record. Records without a value aren't counted.")
c1.caption(md(f"Range {money_range(summary['total_low'], summary['total_high'], cur)}") if summary["valued"] else
           "No valued records yet.")
c2.metric("Records", summary["count"])
c2.caption(f"{summary['valued']} valued · {summary['unvalued']} without a value")
c3.metric("Last refreshed", ago(last_valued))
with c4:
    refresh = st.button("Refresh values", icon=":material/refresh:", type="primary", disabled=not ready or not records,
                        help="Re-prices every matched record and saves a snapshot for the chart. About 2 seconds "
                             "per record with Discogs prices, 3-5 seconds with eBay estimates.")
if summary["mixed_currency"]:
    st.warning("Your records are priced in more than one currency, so the total mixes currencies. "
               "Set DISCOGS_CURRENCY and your Discogs seller currency to match.")

if refresh:
    bar = st.progress(0.0, text="Starting…")

    def on_progress(i, n, rec):
        bar.progress((i - 1) / max(n, 1), text=f"Pricing {i} of {n}: {rec['artist']} – {rec['album']}")

    try:
        result = collection.refresh_all(on_progress)
    except (discogs.DiscogsError, ebay.EbayError) as exc:
        bar.empty()
        st.error(f"Refresh stopped: {exc}", icon=":material/error:")
    else:
        bar.empty()
        msg = f"Refreshed {result['refreshed']} record(s)."
        if result["unmatched"]:
            msg += f" {result['unmatched']} unmatched record(s) skipped."
        if result["snapshot_saved"]:
            msg += " Value snapshot saved."
        else:
            msg += " No value snapshot saved, since no record has a value yet."
        flash("success", msg)
        for rec, why in result["failures"]:
            flash("warning", f"{rec['artist']} – {rec['album']}: {why} (its previous value was kept)")
        st.rerun()

# ----- Most valuable + chart -----

top = valuation.most_valuable(records)
left, right = st.columns([1.3, 1], gap="large")
with left:
    st.subheader("Most valuable")
    if not top:
        st.info("Add and match records to see your most valuable ones here.")
    else:
        cols = st.columns(5)
        for col, rec in zip(cols, top):
            rel = rec["release"] or {}
            with col:
                if rel.get("thumb"):
                    st.image(rel.get("cover") or rel["thumb"], width="stretch")
                st.markdown(md(f"**{money(rec['value']['typical'], rec['value']['currency'])}**"))
                st.caption(f"{rec['artist']} – {rec['album']} · {grading.short(rec['media_condition'])}")

with right:
    st.subheader("Value over time")
    snaps = storage.list_snapshots()
    if not snaps:
        st.info("The chart starts once your records have values." if prices_locked else
                "Each “Refresh values” saves a snapshot. Refresh to start the chart.")
    else:
        df = pd.DataFrame(snaps)
        df["when"] = pd.to_datetime(df["taken_at"]).dt.tz_convert(None)
        df["range"] = [money_range(lo, hi, c) for lo, hi, c in zip(df.total_low, df.total_high, df.currency)]
        df["typical"] = [money(v, c) for v, c in zip(df.total_typical, df.currency)]
        base = alt.Chart(df).encode(x=alt.X("when:T", title=None))
        tooltip = [alt.Tooltip("when:T", title="Date", format="%b %d, %Y %H:%M"),
                   alt.Tooltip("typical:N", title="Typical"), alt.Tooltip("range:N", title="Range"),
                   alt.Tooltip("valued_count:Q", title="Records valued")]
        band = base.mark_area(opacity=0.15, color=ACCENT).encode(
            y=alt.Y("total_low:Q", title=f"Collection value ({cur})", scale=alt.Scale(zero=False)),
            y2="total_high:Q")
        line = base.mark_line(color=ACCENT, strokeWidth=2).encode(y="total_typical:Q")
        points = base.mark_point(color=ACCENT, filled=True, size=70).encode(y="total_typical:Q", tooltip=tooltip)
        st.altair_chart((band + line + points).properties(height=230), use_container_width=True)
        st.caption("Line: typical value. Shaded band: low to high range."
                   + (" Refresh again on another day to see a trend." if len(snaps) == 1 else ""))

st.divider()

# ----- Add a record + match it -----

matching = st.session_state.get("matching")


def start_matching(record_id, artist, album, catno, media, sleeve, notes):
    try:
        candidates, note = search_discogs(artist, album, catno)
    except (discogs.DiscogsError, MissingKeyError) as exc:
        st.error(f"Couldn't search Discogs: {exc}", icon=":material/error:")
        return
    st.session_state["matching"] = dict(record_id=record_id, artist=artist, album=album, catno=catno,
                                        media=media, sleeve=sleeve, notes=notes,
                                        candidates=candidates, note=note)
    st.rerun()


def confirm(release_id: int):
    m = st.session_state["matching"]
    record_id = m["record_id"]
    is_new = record_id is None
    if is_new:
        record_id = storage.add_record(m["artist"], m["album"], m["catno"], m["media"], m["sleeve"], m["notes"])
    try:
        rec = collection.confirm_match(record_id, release_id)
    except (discogs.DiscogsError, MissingKeyError) as exc:
        rec = storage.get_record(record_id)
        if rec and rec["release_id"] == release_id:
            flash("warning", f"Matched “{title_of(rec)}”, but its value couldn't be fetched: {exc}")
        else:
            if is_new:
                storage.delete_record(record_id)
            st.error(f"Couldn't load that release from Discogs: {exc}", icon=":material/error:")
            return
    else:
        v = rec["value"]
        if v.get("locked"):
            flash("info", f"Added “{title_of(rec)}”. Its value is unknown until Discogs price data is turned on.")
        elif v["status"] == valuation.OK:
            flash("success", f"Added “{title_of(rec)}”: typically {money(v['typical'], v['currency'])} "
                             f"({money_range(v['low'], v['high'], v['currency'])}).")
        else:
            flash("info", f"Added “{title_of(rec)}”. {v['reason']}")
    st.session_state.pop("matching", None)
    st.rerun()


if not matching:
    with st.expander("Add a record", icon=":material/add:", expanded=not records):
        with st.form("add_record", clear_on_submit=False, border=False):
            a1, a2, a3 = st.columns(3)
            artist = a1.text_input("Artist *", placeholder="Led Zeppelin")
            album = a2.text_input("Album *", placeholder="Led Zeppelin IV")
            catno = a3.text_input("Catalog number (optional)", placeholder="SD 7208",
                                  help="Printed on the spine, back cover or label. It's the best way to find "
                                       "your exact pressing.")
            b1, b2, b3 = st.columns(3)
            media = b1.selectbox("Record condition *", grading.MEDIA_GRADES, index=None,
                                 placeholder="Choose a grade", help=grading.GRADE_HELP)
            sleeve = b2.selectbox("Sleeve condition", grading.SLEEVE_GRADES, index=None, placeholder="Choose a grade")
            notes = b3.text_input("Notes", placeholder="Grandpa's copy, small seam split")
            submitted = st.form_submit_button("Find on Discogs", icon=":material/search:", disabled=not ready)
        if submitted:
            missing = [n for n, v in [("artist", artist.strip()), ("album", album.strip()), ("record condition", media)] if not v]
            if missing:
                st.error(f"Please fill in: {', '.join(missing)}.")
            else:
                start_matching(None, artist.strip(), album.strip(), catno.strip(), media, sleeve, notes)
else:
    with st.container(border=True):
        verb = "Re-match" if matching["record_id"] else "Match"
        st.subheader(f"{verb}: {matching['artist']} – {matching['album']}")
        st.caption("Pick the pressing that matches yours. Check the label, catalog number and country "
                   "against your record. Pressings with a matching catalog number come first, then the most common.")
        if matching["note"]:
            st.info(matching["note"])
        cands = matching["candidates"]
        if not cands:
            st.warning("Discogs has no vinyl release matching this artist and album. Check the spelling, try fewer "
                       "words, or paste the Discogs link below.")
        for row_start in range(0, len(cands), 3):
            cols = st.columns(3)
            for col, c in zip(cols, cands[row_start:row_start + 3]):
                with col.container(border=True):
                    img, info = st.columns([1, 2])
                    if c["thumb"]:
                        img.image(c["thumb"], width="stretch")
                    info.markdown(f"**{c['artist']} – {c['album']}**")
                    info.caption(" · ".join(str(x) for x in [c["year"] or "year unknown", c["country"]] if x))
                    info.caption(" · ".join(x for x in [", ".join(c["labels"][:2]), c["catno"]] if x))
                    info.caption(", ".join(f for f in c["formats"] if f != "Vinyl"))
                    tag = "✅ Catalog number matches · " if c["catno_match"] else ""
                    info.caption(f"{tag}{c['have']:,} people own this · [view on Discogs]({c['url']})")
                    if st.button("This is my pressing", key=f"pick_{c['release_id']}", width="stretch"):
                        confirm(c["release_id"])

        st.markdown("**Not listed?** Find your pressing on discogs.com and paste its link:")
        p1, p2 = st.columns([3, 1], vertical_alignment="bottom")
        pasted = p1.text_input("Discogs release link or ID", placeholder="https://www.discogs.com/release/1234567",
                               label_visibility="collapsed")
        if p2.button("Use this link", width="stretch"):
            try:
                confirm(discogs.parse_release_ref(pasted))
            except ValueError as exc:
                st.error(str(exc))

        b1, b2, _ = st.columns([1, 1, 2])
        if matching["record_id"] is None and b1.button("Save without a match", help="It's kept in your collection "
                                                       "with no value. You can match it later."):
            storage.add_record(matching["artist"], matching["album"], matching["catno"], matching["media"],
                               matching["sleeve"], matching["notes"])
            flash("info", f"Saved “{matching['artist']} – {matching['album']}” without a Discogs match, so it has no value yet.")
            st.session_state.pop("matching")
            st.rerun()
        if b2.button("Cancel"):
            st.session_state.pop("matching")
            st.rerun()

# ----- The collection table -----

st.subheader(f"All records ({len(records)})")
if not records:
    st.info("Your collection is empty. Add your first record above.")
    st.stop()

query = st.text_input("Filter", placeholder="Search artist, album, label…", label_visibility="collapsed")
rows = []
for rec in records:
    rel = rec.get("release") or {}
    v = rec.get("value") or {}
    ok = v.get("status") == valuation.OK
    rows.append({
        "id": rec["id"],
        "Cover": rel.get("thumb") or None,
        "Artist": rec["artist"],
        "Album": rec["album"],
        "Year": rel.get("year"),
        "Pressing": pressing(rel) or "Not matched",
        "Condition": condition(rec["media_condition"], rec["sleeve_condition"]),
        "Low": v.get("low") if ok else None,
        "Typical": v.get("typical") if ok else None,
        "High": v.get("high") if ok else None,
        "Cheapest listing (not value)": for_sale(rec.get("market"), cur),
        "Note": value_note(rec),
        "Discogs": rel.get("url"),
    })
df = pd.DataFrame(rows)
if query:
    q = query.lower()
    df = df[df[["Artist", "Album", "Pressing"]].apply(lambda r: q in " ".join(r.astype(str)).lower(), axis=1)]

money_fmt = "$%.2f" if cur == "USD" else "%.2f " + cur
st.dataframe(
    df.drop(columns=["id"]), hide_index=True, row_height=60,
    column_config={
        "Cover": st.column_config.ImageColumn(width="small"),
        "Year": st.column_config.NumberColumn(format="%d"),
        "Condition": st.column_config.TextColumn(help="Record / sleeve grade"),
        "Low": st.column_config.NumberColumn(format=money_fmt),
        "Typical": st.column_config.NumberColumn(format=money_fmt),
        "High": st.column_config.NumberColumn(format=money_fmt),
        "Cheapest listing (not value)": st.column_config.TextColumn(
            help="How many copies of this pressing are listed on Discogs right now, and the cheapest asking "
                 "price in any condition (often a worn copy). Not what your record is worth."),
        "Note": st.column_config.TextColumn(width="medium"),
        "Discogs": st.column_config.LinkColumn(display_text="Open"),
    },
)

# ----- Edit one record -----

st.subheader("Edit a record")
by_id = {r["id"]: r for r in records}
chosen = st.selectbox("Record", list(by_id), format_func=lambda i: title_of(by_id[i]), label_visibility="collapsed")
rec = by_id[chosen]
rel = rec.get("release") or {}
v = rec.get("value") or {}

with st.container(border=True):
    pic, details, edit = st.columns([1, 2, 2], gap="medium")
    if rel.get("cover"):
        pic.image(rel["cover"], width="stretch")
    with details:
        st.markdown(f"**{title_of(rec)}**")
        if rec["release_id"]:
            st.caption(pressing(rel))
            if rel.get("genres") or rel.get("styles"):
                st.caption(" · ".join(rel.get("genres", []) + rel.get("styles", [])))
            if v.get("status") == valuation.OK:
                st.markdown(md(f"Typical **{money(v['typical'], v['currency'])}** · "
                               f"range {money_range(v['low'], v['high'], v['currency'])}"))
                st.caption(v["reason"])
            else:
                st.warning(value_note(rec))
            if v.get("source") == "ebay" and v.get("listings"):
                with st.popover(f"eBay listings used ({len(v['listings'])})"):
                    st.caption("Fixed-price listings found for this record, with the grade read from each. "
                               "Auctions, bootlegs and listings that describe damage are left out.")
                    for l in sorted(v["listings"], key=lambda l: l["price"])[:25]:
                        st.caption(md(f"{money(l['price'])} · {grading.short(l['grade']) if l['grade'] else 'grade unknown'}"
                                      f" · [{l['title']}]({l['url']})"))
            market = rec.get("market") or {}
            if market:
                lowest = market.get("lowest_price")
                st.caption(md(f"Right now on Discogs: {market.get('num_for_sale', 0)} for sale"
                              + (f", cheapest {money(lowest, market.get('currency') or cur)} (any condition)" if lowest is not None else "")))
            if rec.get("suggestions"):
                with st.popover("Price by condition"):
                    st.table(pd.DataFrame(
                        [{"Grade": g, "Suggested": money(rec["suggestions"][g], v.get("currency") or cur)}
                         for g in grading.MEDIA_GRADES if g in rec["suggestions"]]).set_index("Grade"))
            st.caption(f"Valued {ago(rec['valued_at'])} · [Open on Discogs]({rel.get('url')})")
        else:
            st.warning("Not matched to a Discogs release, so it has no value. Use “Match on Discogs”.")
        if rec["catno_entered"]:
            st.caption(f"Catalog number you entered: {rec['catno_entered']}")

    with edit:
        with st.form(f"edit_{chosen}", border=False):
            media_i = grading.MEDIA_GRADES.index(rec["media_condition"]) if rec["media_condition"] in grading.MEDIA_GRADES else None
            sleeve_i = grading.SLEEVE_GRADES.index(rec["sleeve_condition"]) if rec["sleeve_condition"] in grading.SLEEVE_GRADES else None
            new_media = st.selectbox("Record condition", grading.MEDIA_GRADES, index=media_i, help=grading.GRADE_HELP)
            new_sleeve = st.selectbox("Sleeve condition", grading.SLEEVE_GRADES, index=sleeve_i)
            new_notes = st.text_input("Notes", value=rec["notes"] or "")
            if st.form_submit_button("Save changes"):
                collection.change_condition(chosen, new_media, new_sleeve, new_notes)
                flash("success", "Saved. The value was re-estimated from the saved Discogs prices.")
                st.rerun()

        e1, e2, e3 = st.columns(3)
        if e1.button("Refresh value", disabled=not ready or not rec["release_id"], width="stretch"):
            try:
                collection.revalue_record(chosen)
                flash("success", "Value refreshed.")
                st.rerun()
            except (discogs.DiscogsError, MissingKeyError, ValueError) as exc:
                st.error(f"Couldn't refresh: {exc}")
        if e2.button("Re-match" if rec["release_id"] else "Match on Discogs", disabled=not ready, width="stretch"):
            start_matching(chosen, rec["artist"], rec["album"], rec["catno_entered"] or "",
                           rec["media_condition"], rec["sleeve_condition"], rec["notes"])
        with e3.popover("Delete", width="stretch"):
            st.write("Remove this record from your collection?")
            if st.button("Yes, delete", type="primary", key=f"del_{chosen}"):
                storage.delete_record(chosen)
                flash("info", f"Deleted “{title_of(rec)}”.")
                st.rerun()
