"""Loads API keys and settings from .env or .streamlit/secrets.toml. Keys never live in code."""

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SEED_DB = PROJECT_ROOT / "sample_data" / "collection_seed.db"  # starting collection for a fresh copy of the app

load_dotenv(PROJECT_ROOT / ".env")

_SECRETS_FILES = (PROJECT_ROOT / ".streamlit" / "secrets.toml", Path.home() / ".streamlit" / "secrets.toml")


class MissingKeyError(RuntimeError):
    """Raised when a required API key is not set."""


def _setting(name: str) -> str:
    """Looks in the environment / .env first, then Streamlit secrets (if a secrets file exists)."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    if any(p.exists() for p in _SECRETS_FILES):
        try:
            import streamlit as st

            return str(st.secrets.get(name, "")).strip()
        except Exception:
            return ""
    return ""


def get_discogs_token() -> str:
    token = _setting("DISCOGS_TOKEN")
    if not token:
        raise MissingKeyError(
            "DISCOGS_TOKEN is not set. Create a free token at discogs.com → Settings → Developers "
            "→ “Generate new token”, paste it into the .env file (see README.md), then restart the app."
        )
    return token


def get_currency() -> str:
    return (_setting("DISCOGS_CURRENCY") or "USD").upper()


def db_path() -> Path:
    """Where the app's data is saved. VINYL_DB_PATH overrides it (the tests use this)."""
    override = os.getenv("VINYL_DB_PATH", "").strip()
    if override:
        return Path(override)
    path = DATA_DIR / "vinyl_agent.db"
    if not path.exists() and SEED_DB.exists():
        # First run of a fresh copy (for example the hosted demo): start from the sample collection.
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SEED_DB, path)
    return path


def get_ebay_credentials():
    """(client id, client secret) for eBay's Browse API. Both from one free eBay developer keyset."""
    client_id, secret = _setting("EBAY_CLIENT_ID"), _setting("EBAY_CLIENT_SECRET")
    if not client_id or not secret:
        raise MissingKeyError(
            "eBay keys aren't set, so only Discogs is searched. To add eBay listings, create a free "
            "developer account at developer.ebay.com, make a Production keyset, and put its App ID and "
            "Cert ID in .env as EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (see README.md). Then restart the app."
        )
    return client_id, secret


def get_ebay_zip() -> str:
    """Optional: your ZIP code, so eBay can work out shipping costs to you."""
    return _setting("EBAY_ZIP")


def get_lastfm_key() -> str:
    """Last.fm API key, used for "similar artists" in Discover."""
    key = _setting("LASTFM_API_KEY")
    if not key:
        raise MissingKeyError(
            "LASTFM_API_KEY isn't set, so recommendations are off. Get a free key at "
            "last.fm/api/account/create (any app name; leave the callback URL blank), put the "
            "“API key” in .env as LASTFM_API_KEY, then restart the app."
        )
    return key


def get_spotify_client_id() -> str:
    """Spotify app Client ID (login uses PKCE, so no client secret is needed)."""
    cid = _setting("SPOTIFY_CLIENT_ID")
    if not cid:
        raise MissingKeyError(
            "SPOTIFY_CLIENT_ID isn't set, so Spotify login is off. Create a free app at "
            "developer.spotify.com/dashboard, add the redirect URI " + get_spotify_redirect_uri() +
            ", tick “Web API”, then put its Client ID in .env as SPOTIFY_CLIENT_ID and restart the app."
        )
    return cid


def get_spotify_redirect_uri() -> str:
    """Where Spotify sends you back after logging in. Must match the app's settings on Spotify exactly."""
    return _setting("SPOTIFY_REDIRECT_URI") or "http://127.0.0.1:8501/discover"


def get_musicbrainz_contact() -> str:
    """Optional contact (email or URL) for MusicBrainz's required User-Agent."""
    return _setting("MUSICBRAINZ_CONTACT")


def get_anthropic_key() -> str:
    """Optional, paid: Claude reads listing descriptions and picks recommendations. Empty = free rules only."""
    return _setting("ANTHROPIC_API_KEY")


def get_claude_model() -> str:
    """The model for jobs that need judgment (picking recommendations)."""
    return _setting("CLAUDE_MODEL") or "claude-sonnet-5-5"


def get_claude_fast_model() -> str:
    """The model for bulk reading (grading listing descriptions)."""
    return _setting("CLAUDE_FAST_MODEL") or "claude-haiku-5-5"


def claude_in_daily_check() -> bool:
    """Whether the unattended daily check may spend Claude credit. Off unless CLAUDE_DAILY_CHECK=1."""
    return _setting("CLAUDE_DAILY_CHECK").lower() in ("1", "true", "yes", "on")
