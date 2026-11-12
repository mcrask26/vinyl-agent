"""Small Streamlit helpers shared by the pages."""

import streamlit as st


def md(text: str) -> str:
    """Escape $ so Streamlit's markdown doesn't treat "$18 – $45" as a math formula."""
    return (text or "").replace("$", "\\$")


def flash(kind: str, text: str) -> None:
    """Show a message after the next rerun (st.rerun clears anything shown before it)."""
    st.session_state.setdefault("flash", []).append((kind, md(text)))


def show_flash() -> None:
    for kind, text in st.session_state.pop("flash", []):
        getattr(st, kind)(text)
