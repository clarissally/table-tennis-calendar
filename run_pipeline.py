"""
run_pipeline.py

The single entrypoint a scheduled job (cron / GitHub Actions) should
call. Wires the four building-block modules together end to end:

  weibo_scraper.fetch_recent_posts()
      -> schedule_parser.parse_post() per post
      -> state_store.apply_events() per player, diffed against on-disk state
      -> ics_generator.write_feed() per player

Designed to be safe to run on a tight, unattended schedule (every
30-60 minutes per the design doc): every step is idempotent, and a
post that's already fully reflected in state produces no changes.

Usage:
    python3 run_pipeline.py [--feeds-dir feeds] [--max-pages 1]

Exit code is non-zero only on an unexpected error (e.g. the scraper
failing); "nothing new this run" is not an error.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from schedule_parser import parse_post, TARGET_PLAYERS
from state_store import apply_events
from ics_generator import write_feed
from weibo_scraper import fetch_recent_posts, WeiboFetchError

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def run(feeds_dir: str, max_pages: int) -> int:
    try:
        posts = fetch_recent_posts(max_pages=max_pages)
    except WeiboFetchError as e:
        print(f"ERROR: failed to fetch posts: {e}", file=sys.stderr)
        return 1

    print(f"Fetched {len(posts)} post(s).")

    events_by_player = {tag: [] for tag in TARGET_PLAYERS.values()}

    for post in posts:
        result = parse_post(post.text, source_post_id=post.mid)
        if result.notes:
            for note in result.notes:
                print(f"  [{post.mid}] NOTE: {note}")
        for event in result.events:
            for tag in event.player_tags:
                events_by_player[tag].append(event)

    any_review_items = False
    for tag, events in events_by_player.items():
        update = apply_events(tag, events)
        print(
            f"[{tag}] new={len(update.new_uids)} changed={len(update.changed_uids)} "
            f"unchanged={len(update.unchanged_uids)} held_for_review={len(update.held_for_review)}"
        )
        if update.held_for_review:
            any_review_items = True
        path = write_feed(tag, update.publishable, output_dir=feeds_dir)
        print(f"[{tag}] wrote {path} ({len(update.publishable)} event(s))")

    if any_review_items:
        print(
            "\nSome events are held for manual review -- see "
            f"{os.path.join('data', 'review_queue.json')} and tools/promote.py."
        )

    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feeds-dir", default=os.path.join(PROJECT_ROOT, "feeds"))
    parser.add_argument("--max-pages", type=int, default=1)
    args = parser.parse_args()

    try:
        sys.exit(run(args.feeds_dir, args.max_pages))
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
