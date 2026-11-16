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


def claude_status() -> None:
    """One line saying whether Claude is helping on this page and what it has cost so far."""
    from tools import claude

    if not claude.enabled():
        st.caption(":material/smart_toy: Claude is off, so the app uses its built-in rules (free). "
                   "Add ANTHROPIC_API_KEY to .env to turn it on.")
        return
    used = claude.usage()
    spent = "nothing spent yet" if not used["calls"] else \
        f"{used['calls']} request{'s' if used['calls'] != 1 else ''}, about ${used['cost']:.2f} spent so far" \
        if used["cost"] >= 0.005 else f"{used['calls']} request{'s' if used['calls'] != 1 else ''}, under 1 cent spent so far"
    st.caption(md(f":material/smart_toy: Claude is on · {spent}"))
