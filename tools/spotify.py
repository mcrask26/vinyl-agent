"""Spotify Web API: your top artists and tracks. Free, needs SPOTIFY_CLIENT_ID.

Login uses Spotify's "authorization code with PKCE" flow, so no client secret is stored.
Only one permission is asked for: user-top-read (your top artists and tracks). Read-only.
Docs: https://developer.spotify.com/documentation/web-api
"""

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import requests

from core.config import get_spotify_client_id, get_spotify_redirect_uri

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPE = "user-top-read"
TIME_RANGES = {"short_term": "Last 4 weeks", "medium_term": "Last 6 months", "long_term": "Last year or so"}
TIMEOUT_S = 20


class SpotifyError(RuntimeError):
    def __init__(self, message: str, fatal: bool = False, relogin: bool = False):
        super().__init__(message)
        self.fatal = fatal
        self.relogin = relogin


def new_login() -> dict:
    """Returns {"url", "state", "verifier"}. Save state+verifier until Spotify sends you back."""
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    params = {"client_id": get_spotify_client_id(), "response_type": "code", "redirect_uri": get_spotify_redirect_uri(),
              "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPE, "state": state}
    return {"url": f"{AUTH_URL}?{urlencode(params)}", "state": state, "verifier": verifier}


def _token_request(data: dict) -> dict:
    try:
        resp = requests.post(TOKEN_URL, data={**data, "client_id": get_spotify_client_id()}, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise SpotifyError(f"Couldn't reach Spotify (are you offline?). Details: {exc}", fatal=True) from exc
    if resp.status_code in (400, 401):
        msg = ""
        try:
            msg = resp.json().get("error_description", "")
        except ValueError:
            pass
        raise SpotifyError(f"Spotify login didn't work ({msg or 'rejected'}). Please log in again.", relogin=True)
    if not resp.ok:
        raise SpotifyError(f"Spotify login failed (HTTP {resp.status_code}). Try again later.", fatal=True)
    body = resp.json()
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token") or data.get("refresh_token"),
            "expires_at": time.time() + int(body.get("expires_in", 3600)) - 60}


def exchange_code(code: str, verifier: str) -> dict:
    return _token_request({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": get_spotify_redirect_uri(), "code_verifier": verifier})


def refresh(token: dict) -> dict:
    if not token.get("refresh_token"):
        raise SpotifyError("Your Spotify login expired. Please log in again.", relogin=True)
    return _token_request({"grant_type": "refresh_token", "refresh_token": token["refresh_token"]})


def _get(path: str, token: dict, params=None) -> dict:
    for attempt in range(3):
        try:
            resp = requests.get(f"{API}{path}", params=params, timeout=TIMEOUT_S,
                                headers={"Authorization": f"Bearer {token['access_token']}"})
        except requests.RequestException as exc:
            raise SpotifyError(f"Couldn't reach Spotify (are you offline?). Details: {exc}", fatal=True) from exc
        if resp.status_code == 429 and attempt < 2:
            time.sleep(min(int(resp.headers.get("Retry-After", "5")), 30))
            continue
        if resp.status_code == 401:
            raise SpotifyError("Your Spotify login expired. Please log in again.", relogin=True)
        if resp.status_code == 403:
            raise SpotifyError("Spotify refused access. In your app on developer.spotify.com, open User Management "
                               "and add the email of the Spotify account you log in with, then log in again.",
                               relogin=True)
        if not resp.ok:
            raise SpotifyError(f"Spotify error (HTTP {resp.status_code}). Try again later.", fatal=resp.status_code >= 500)
        return resp.json()
    raise SpotifyError("Spotify is rate-limiting us. Try again in a minute.", fatal=True)


def parse_artist(a: dict) -> dict:
    images = a.get("images") or []
    return {"name": a.get("name", ""), "genres": a.get("genres") or [], "image": images[-1]["url"] if images else "",
            "url": (a.get("external_urls") or {}).get("spotify", "")}


def parse_track(t: dict) -> dict:
    album = t.get("album") or {}
    images = album.get("images") or []
    return {"name": t.get("name", ""), "artists": [a.get("name", "") for a in t.get("artists") or []],
            "album": album.get("name", ""), "album_type": album.get("album_type", ""),
            "year": (album.get("release_date") or "")[:4], "image": images[-1]["url"] if images else "",
            "url": (t.get("external_urls") or {}).get("spotify", "")}


def top_items(token: dict) -> dict:
    """{"artists": {range: [...]}, "tracks": {range: [...]}} for all three time ranges (6 calls)."""
    out = {"artists": {}, "tracks": {}}
    for rng in TIME_RANGES:
        out["artists"][rng] = [parse_artist(a) for a in
                               _get("/me/top/artists", token, {"time_range": rng, "limit": 50}).get("items") or []]
        out["tracks"][rng] = [parse_track(t) for t in
                              _get("/me/top/tracks", token, {"time_range": rng, "limit": 50}).get("items") or []]
    return out
