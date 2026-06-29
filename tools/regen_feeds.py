"""
tools/regen_feeds.py

Re-renders both .ics feeds straight from on-disk state, without
touching the scraper or the parser. Use this after tools/promote.py
(which updates state but, per its own docstring, does not regenerate
the feeds itself) -- or any other time the feeds directory needs to
catch up with data/state_*.json without re-running the whole pipeline.

Usage:
    python3 tools/regen_feeds.py [--feeds-dir feeds]
"""

from __future__ import annotations

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from schedule_parser import TARGET_PLAYERS  # noqa: E402
from state_store import load_state  # noqa: E402
from ics_generator import write_feed  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feeds-dir", default=os.path.join(PROJECT_ROOT, "feeds"))
    args = parser.parse_args()

    for tag in TARGET_PLAYERS.values():
        state = load_state(tag)
        publishable = [ev for ev in state.values() if ev.confidence == "high"]
        path = write_feed(tag, publishable, output_dir=args.feeds_dir)
        print(f"[{tag}] wrote {path} ({len(publishable)} event(s))")


if __name__ == "__main__":
    main()
