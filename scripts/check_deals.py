"""Daily deal check: searches your whole wishlist and saves new Great Deals as alerts.

Run it by hand:      .venv/bin/python scripts/check_deals.py
Run it every day:    see scripts/schedule_daily_check.sh (macOS) or the README.

It never buys anything. It only saves alerts; open the Alerts page and click "Buy now"
to go to the listing, where you decide and pay yourself.

Options:
  --no-notify   don't show a macOS notification when new deals are found
Exit code 0 = ran fine (even with some search problems), 1 = couldn't run at all.
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import alerts  # noqa: E402
from core.config import MissingKeyError  # noqa: E402
from core.format import money  # noqa: E402


def notify(title: str, text: str) -> None:
    """macOS notification banner (free, built in). Silently skipped elsewhere."""
    if sys.platform != "darwin":
        return
    quote = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    subprocess.run(["osascript", "-e", f'display notification "{quote(text)}" with title "{quote(title)}"'],
                   check=False, capture_output=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-notify", action="store_true", help="don't show a macOS notification")
    no_notify = parser.parse_args(argv).no_notify
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        result = alerts.check_now(trigger="daily script")
    except MissingKeyError as exc:
        print(f"[{stamp}] Couldn't run: {exc}")
        return 1
    print(f"[{stamp}] {alerts.summary_text(result)}")
    for a in result["new"]:
        print(f"  NEW  {a['artist']} – {a['album']}: {money(a['total'], a['currency'])} on {a['source']}  {a['url']}")
    for e in result["errors"]:
        print(f"  PROBLEM  {e['wish']} ({e['source']}): {e['message']}")
    if result["new"] and not no_notify:
        first = result["new"][0]
        more = f" and {len(result['new']) - 1} more" if len(result["new"]) > 1 else ""
        notify("Vinyl Agent: new Great Deal" + ("s" if more else ""),
               f"{first['artist']} – {first['album']} for {money(first['total'], first['currency'])}{more}. "
               "Open the Alerts page to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
