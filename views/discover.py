"""Discover: recommendations from your collection, Spotify vs your shelf, and Missing Pieces."""

import streamlit as st

from core import deals, discover, storage
from core.config import (MissingKeyError, get_lastfm_key, get_spotify_client_id, get_spotify_redirect_uri)
from core.format import ago, money
from core.names import norm_artist
from core.ui import flash, md, show_flash
from tools import claude, spotify

RATING_BADGE = {deals.GREAT: "🟢 Great Deal", deals.FAIR: "🟡 Fair", deals.OVER: "🔴 Overpriced",
                deals.UNRATED: "⚪ Not rated"}


def key_missing(check):
    try:
        check()
        return None
    except MissingKeyError as exc:
        return str(exc)


def price_lines(p: dict) -> list:
    """Short lines describing what an album costs right now."""
    if not p:
        return []
    lines = []
    if p.get("ebay"):
        e = p["ebay"]
        lines.append(f"{RATING_BADGE[e['rating']]} · [{money(e['total'])} on eBay]({e['url']}) · {e['reason']}")
    if p.get("discogs_lowest") is not None:
        where = f" ({p['discogs_pressing']} pressing)" if p.get("discogs_pressing") else ""
        lines.append(f"Vinyl from [{money(p['discogs_lowest'])} on Discogs]({p['discogs_url']}){where}, "
                     f"any condition, plus shipping · {p['discogs_count']} for sale")
    lines += p.get("notes") or []
    return lines


def add_button(artist, album, key, master_id=None, note=""):
    if st.button("Add to wishlist", key=key, icon=":material/add:"):
        res = discover.add_to_wishlist(artist, album, master_id=master_id, note=note)
        if res["already"]:
            flash("info", f"“{artist} – {album}” is already on your wishlist.")
        else:
            flash("success", f"Added “{artist} – {album}” to your wishlist (any vinyl pressing, VG+ or better, no max "
                             "price). Set a max price on the Wishlist page; it's searched on the next check.")
        st.rerun()


def progress_bar(text):
    bar = st.progress(0.0, text=text)
    return bar, (lambda frac, msg: bar.progress(min(frac, 1.0), text=msg))


# ---------- Spotify login: Spotify sends you back here with ?code=…&state=… ----------

params = st.query_params
if "code" in params or "error" in params:
    pending = storage.get_state("spotify_pending", {})
    state = params.get("state", "")
    if "error" in params:
        flash("warning", "Spotify login was cancelled." if params["error"] == "access_denied"
              else f"Spotify login failed: {params['error']}")
    elif state not in pending:
        flash("warning", "That Spotify login link was old or already used. Click “Log in with Spotify” again.")
    else:
        try:
            token = spotify.exchange_code(params["code"], pending.pop(state))
            storage.set_state("spotify_token", token)
            storage.set_state("spotify_pending", pending)
            with st.spinner("Getting your top artists and tracks from Spotify…"):
                storage.set_state("spotify_top", spotify.top_items(token))
            flash("success", "Connected to Spotify and loaded your top artists and tracks.")
        except (spotify.SpotifyError, MissingKeyError) as exc:
            flash("error", f"Spotify: {exc}")
    st.query_params.clear()
    st.rerun()


# ---------- Page ----------

st.title("Discover")
st.caption("New records picked from what you own and what you stream. Nothing here is bought: "
           "you can add things to your wishlist, and the deal finder takes it from there.")
show_flash()

records = storage.list_records()
wishes = storage.list_wishes()
tab_recs, tab_spotify, tab_missing = st.tabs(["Recommended for you", "Spotify vs your shelf", "Missing Pieces"])

# ----- (a) Recommendations -----

with tab_recs:
    lastfm_missing = key_missing(get_lastfm_key)
    profile = discover.taste_profile(records)
    if not records:
        st.info("Add records to **My Collection** first. Recommendations are based on what you own.")
    else:
        with st.expander("Your taste profile", icon=":material/insights:", expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.markdown("**Most-owned artists**")
            for name, n in profile["artists"][:8]:
                c1.caption(f"{name} · {n} record{'s' if n != 1 else ''}")
            c2.markdown("**Styles**")
            for name, n in profile["styles"].most_common(8):
                c2.caption(f"{name} · {n}")
            if not profile["styles"]:
                c2.caption("Match records to Discogs on My Collection to see styles.")
            c3.markdown("**Decades**")
            for name, n in sorted(profile["decades"].items()):
                c3.caption(f"{name} · {n}")
            if profile["with_details"] < profile["count"]:
                st.caption(f"{profile['count'] - profile['with_details']} of your records aren't matched to Discogs, "
                           "so their genres and years aren't counted.")

    if lastfm_missing:
        st.warning(lastfm_missing, icon=":material/key:")
    saved = storage.get_state("recommendations")
    sp_top = storage.get_state("spotify_top")
    b1, b2 = st.columns([1, 2], vertical_alignment="center")
    use_claude = False
    if claude.enabled():
        use_claude = b2.toggle("Friendlier wording by Claude (uses your paid Anthropic key)", value=False)
    if b1.button("Find recommendations" if not saved else "Refresh recommendations", type="primary",
                 icon=":material/auto_awesome:", disabled=bool(lastfm_missing) or not records, width="stretch"):
        bar, prog = progress_bar("Reading your collection…")
        spotify_names = [a["name"] for a in (sp_top or {}).get("artists", {}).get("medium_term", [])]
        try:
            result = discover.recommend(records, wishes, spotify_names, on_progress=prog)
        except discover.DiscoverError as exc:
            bar.empty()
            st.error(str(exc))
        else:
            if use_claude:
                better = claude.rewrite_reasons([i["reason"] for i in result["items"]])
                if better:
                    for item, text in zip(result["items"], better):
                        item["reason"] = text
                else:
                    result["notes"].append("Claude wasn't available, so the standard reasons are shown.")
            storage.set_state("recommendations", result)
            bar.empty()
            st.rerun()

    if saved:
        st.caption(f"Picked {ago(storage.state_updated_at('recommendations'))} from similar artists to "
                   f"{', '.join(saved['seeds'][:5])}{'…' if len(saved['seeds']) > 5 else ''} (Last.fm) "
                   "and your Discogs styles.")
        if not saved["items"]:
            st.info("No recommendations found. Add more records, or match them to Discogs, and try again.")
        hidden = storage.hidden_suggestions("artist")
        for n, item in enumerate(saved["items"], start=1):
            if norm_artist(item["artist"]) in hidden:
                continue
            with st.container(border=True):
                img, body, act = st.columns([1, 4, 1.3])
                if item.get("image"):
                    img.image(item["image"], width="stretch")
                title = f"**{n}. {item['artist']}**" + (f" – *{item['album']}*" if item.get("album") else "")
                if item.get("year"):
                    title += f" ({item['year']})"
                body.markdown(md(title))
                body.write(md(item["reason"]))
                tags = (item.get("styles") or item.get("genres") or [])[:4]
                if tags:
                    body.caption(" · ".join(tags))
                for line in price_lines(item.get("prices")):
                    body.caption(md(line))
                with act:
                    if item.get("album") and not item.get("on_wishlist"):
                        add_button(item["artist"], item["album"], f"rec_add_{n}", item.get("master_id"),
                                   note="Recommended in Discover")
                    elif item.get("on_wishlist"):
                        st.caption("On your wishlist ✓")
                    if item.get("discogs_url"):
                        st.link_button("Discogs", item["discogs_url"], width="stretch")
                    if st.button("Not interested", key=f"rec_hide_{n}", width="stretch"):
                        storage.hide_suggestion("artist", norm_artist(item["artist"]))
                        st.rerun()
        for note in saved.get("notes") or []:
            st.caption(md(note))

# ----- (b) Spotify -----

with tab_spotify:
    sp_missing = key_missing(get_spotify_client_id)
    token = storage.get_state("spotify_token")
    if sp_missing:
        st.warning(sp_missing, icon=":material/key:")
        st.caption(f"In the Spotify app settings the Redirect URI must be exactly `{get_spotify_redirect_uri()}`.")
    else:
        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="center")
        if token:
            c1.success(f"Connected to Spotify · data from {ago(storage.state_updated_at('spotify_top'))}")
            if c2.button("Reload from Spotify", icon=":material/refresh:", width="stretch"):
                try:
                    if token["expires_at"] < __import__("time").time():
                        token = spotify.refresh(token)
                        storage.set_state("spotify_token", token)
                    with st.spinner("Getting your top artists and tracks…"):
                        storage.set_state("spotify_top", spotify.top_items(token))
                    flash("success", "Reloaded your Spotify top artists and tracks.")
                except spotify.SpotifyError as exc:
                    if exc.relogin:
                        storage.delete_state("spotify_token")
                    flash("error", f"Spotify: {exc}")
                st.rerun()
            if c3.button("Disconnect", width="stretch"):
                for k in ("spotify_token", "spotify_top", "spotify_prices"):
                    storage.delete_state(k)
                flash("info", "Disconnected from Spotify and deleted the saved Spotify data.")
                st.rerun()
        else:
            pending = storage.get_state("spotify_pending", {})
            if st.session_state.get("sp_state") not in pending:
                login = spotify.new_login()  # one login link per browser tab, kept until used
                pending = dict(list(pending.items())[-4:] + [(login["state"], login["verifier"])])
                storage.set_state("spotify_pending", pending)
                st.session_state["sp_login"] = login
                st.session_state["sp_state"] = login["state"]
            c1.write("Log in so the app can see your top artists and tracks (read-only; it can't play, "
                     "change or post anything).")
            c2.link_button("Log in with Spotify", st.session_state["sp_login"]["url"], type="primary",
                           icon=":material/login:", width="stretch")
            c3.caption("Opens in a new tab. You come back here, logged in.")

    top = storage.get_state("spotify_top")
    if top and records:
        lists = discover.spotify_lists(records, top)
        prices = storage.get_state("spotify_prices", {})
        s1, s2, s3 = st.tabs([f"Streamed and owned ({len(lists['both'])})",
                              f"Streamed, not owned ({len(lists['streamed_only'])})",
                              f"Owned, not streamed ({len(lists['owned_only'])})"])
        with s1:
            st.caption("Artists in your Spotify top lists that you also have on vinyl.")
            for e in lists["both"]:
                line = f"**{e['name']}** · you own {', '.join(e['owned'])}"
                if e.get("top_album") and not e["owned_top_album"]:
                    line += f" · you stream *{e['top_album']}* most, which you don't own"
                st.markdown(md(line))
                st.caption(" · ".join(e["ranges"]) or f"{e['tracks']} of your top tracks")
            if not lists["both"]:
                st.caption("None yet.")
        with s2:
            st.caption("Artists you stream a lot but don't own any records by, with prices for the album you play most.")
            todo = [e for e in lists["streamed_only"][:10] if e.get("top_album")]
            if st.button(f"Look up prices for the top {len(todo)}", icon=":material/sell:", disabled=not todo,
                         help="About 2 Discogs calls and 1 eBay call per album."):
                bar, prog = progress_bar("Checking prices…")
                for i, e in enumerate(todo, start=1):
                    prog(i / len(todo), f"Checking {e['name']} – {e['top_album']}…")
                    try:
                        info = discover._discogs_album(e["name"], e["top_album"])
                    except Exception:
                        info = None
                    prices[f"{e['name']}|{e['top_album']}"] = discover.cached_price_check(
                        e["name"], e["top_album"], info["master_id"] if info else None)
                storage.set_state("spotify_prices", prices)
                bar.empty()
                st.rerun()
            for i, e in enumerate(lists["streamed_only"][:30]):
                with st.container(border=True):
                    body, act = st.columns([5, 1.3])
                    line = f"**{e['name']}**" + (f" – *{e['top_album']}*" if e.get("top_album") else "")
                    body.markdown(md(line))
                    body.caption(" · ".join(e["ranges"] + ([f"{e['tracks']} of your top tracks"] if e["tracks"] else [])))
                    for pl in price_lines(prices.get(f"{e['name']}|{e.get('top_album')}")):
                        body.caption(md(pl))
                    if e.get("top_album"):
                        with act:
                            add_button(e["name"], e["top_album"], f"sp_add_{i}", note="From your Spotify listening")
        with s3:
            st.caption("Artists you own on vinyl who aren't in your Spotify top 50 artists or tracks for any period. "
                       "Spotify only shares your top lists, not your full history, so you may still play them now and then.")
            for e in lists["owned_only"]:
                st.markdown(md(f"**{e['name']}** · {', '.join(e['owned'])}"))
            if not lists["owned_only"]:
                st.caption("None: you stream everyone you own.")
    elif top and not records:
        st.info("Add records to My Collection to compare them with your Spotify listening.")

# ----- (c) Missing Pieces -----

with tab_missing:
    st.caption("Studio albums (from MusicBrainz) by artists you own 2 or more records by, so you can complete "
               "their discographies. Live albums and compilations are left out.")
    saved_mp = storage.get_state("missing_pieces")
    eligible = [a for a, n in discover.taste_profile(records)["artists"] if n >= 2]
    if not eligible:
        st.info("You don't own 2 or more records by any artist yet. Add more to My Collection.")
    if st.button("Find missing albums" if not saved_mp else "Refresh", type="primary", icon=":material/library_add:",
                 disabled=not eligible, help=f"About 2 MusicBrainz calls per artist ({len(eligible)} artists), "
                 "1 per second; saved for 2 weeks."):
        bar, prog = progress_bar("Asking MusicBrainz…")
        try:
            saved_mp = discover.missing_pieces(records, wishes, on_progress=prog)
            storage.set_state("missing_pieces", saved_mp)
        except discover.DiscoverError as exc:
            st.error(str(exc))
        bar.empty()
        st.rerun()

    if saved_mp:
        # Re-check wishlist / "I own this" status without new calls.
        marked = storage.hidden_suggestions("owned_album")
        wish_keys = {(norm_artist(w["artist"]), w["album"]) for w in wishes}
        show_owned = st.toggle("Show albums I already own", value=False)
        for a in saved_mp["artists"]:
            missing = [al for al in a["albums"] if al["status"] == "missing" and al["key"] not in marked]
            label = f"**{a['artist']}** · you own {a['owned_count']} · {len(missing)} missing studio album" \
                    f"{'s' if len(missing) != 1 else ''}"
            with st.expander(md(label), expanded=bool(missing) and len(saved_mp["artists"]) <= 3):
                for j, al in enumerate(a["albums"]):
                    status = "owned" if al["key"] in marked else al["status"]
                    if any(norm_artist(a["artist"]) == wa and discover.album_matches(al["title"], wt)
                           for wa, wt in wish_keys):
                        status = "wishlist"
                    if status == "owned" and not show_owned:
                        continue
                    c1, c2, c3 = st.columns([4, 1.3, 1.3], vertical_alignment="center")
                    badge = {"owned": "✅ Owned", "wishlist": "⭐ On wishlist", "missing": "➕ Missing"}[status]
                    c1.markdown(md(f"{badge} · **{al['title']}**" + (f" ({al['year']})" if al["year"] else "")))
                    if status == "missing":
                        with c2:
                            add_button(a["artist"], al["title"], f"mp_add_{a['artist']}_{j}",
                                       note="From Missing Pieces")
                        if c3.button("I own this", key=f"mp_own_{a['artist']}_{j}", width="stretch",
                                     help="Marks it as owned here (doesn't add it to My Collection)."):
                            storage.hide_suggestion("owned_album", al["key"])
                            st.rerun()
                st.caption(f"[{a['artist']} on MusicBrainz]({a['mb_url']})")
        for note in saved_mp.get("notes") or []:
            st.caption(md(note))
        st.caption(f"Discographies from MusicBrainz, {ago(storage.state_updated_at('missing_pieces'))}.")
