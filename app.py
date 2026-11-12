"""Vinyl Agent: value your collection, find deals, discover records.

Run with:  streamlit run app.py
Pages live in views/.
"""

import streamlit as st

from core import storage

st.set_page_config(page_title="Vinyl Agent", page_icon="💿", layout="wide")

new_alerts = storage.count_new_alerts()

pages = [
    st.Page("views/collection.py", title="My Collection", icon=":material/album:", default=True),
    st.Page("views/wishlist.py", title="Wishlist & Deals", icon=":material/sell:"),
    st.Page("views/alerts.py", title=f"Alerts ({new_alerts})" if new_alerts else "Alerts",
            icon=":material/notifications:", url_path="alerts"),
    st.Page("views/discover.py", title="Discover", icon=":material/explore:", url_path="discover"),
]

st.navigation(pages).run()
