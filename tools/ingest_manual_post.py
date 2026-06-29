"""
tools/ingest_manual_post.py

Manual / "Plan B" intake path (design doc Section 3, "视觉/OCR兜底"):
feeds one already-transcribed post's text through the exact same
parse -> diff -> ICS pipeline that run_pipeline.py uses for
live-scraped posts, WITHOUT going through weibo_scraper.py at all.

This is the entry point for the screenshot fallback: when the
automated scraper (cloud-IP-blocked, or waiting on the self-hosted
runner) can't reach Weibo, a screenshot of a real schedule post is
transcribed into the same plain-text shape the account actually posts
(see schedule_parser.py module docstring for the exact shape), and that
text is run through this script instead. schedule_parser.py /
state_store.py / ics_generator.py underneath are 100% shared with the
automated path -- this file only supplies a different "where did the
text come from" entry point. Nothing about confidence gating, UID
stability, or SEQUENCE bumping is duplicated or special-cased here.

Usage:
    python3 tools/ingest_manual_post.py --post-id <id> --text-file <path> [--feeds-dir feeds]
    cat post.txt | python3 tools/ingest_manual_post.py --post-id <id>

Exit code is non-zero only on an unexpected error. "Post parsed but
contained no Wang Chuqin / Sun Yingsha matches" is not an error -- it
just means there's nothing to publish from this particular post.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from schedule_parser import parse_post, TARGET_PLAYERS  # noqa: E402
from state_store import apply_events  # noqa: E402
from ics_generator import write_feed  # noqa: E402


def run(text: str, post_id: str, feeds_dir: str) -> int:
    result = parse_post(text, source_post_id=post_id)
    print(f"classification: {result.post_classification}")
    for note in result.notes:
        print(f"  NOTE: {note}")

    if result.post_classification != "schedule":
        print("Nothing to publish (post is not a schedule post).")
        return 0

    events_by_player = {tag: [] for tag in TARGET_PLAYERS.values()}
    for event in result.events:
        for tag in event.player_tags:
            events_by_player[tag].append(event)

    found_any = any(events_by_player.values())
    if not found_any:
        print("No Wang Chuqin / Sun Yingsha matches found in this post.")

    # Mirror run_pipeline.py: always run apply_events/write_feed for both
    # tags, even ones with zero events in THIS post, so each feed still
    # reflects every previously-published high-confidence event (see
    # apply_events()'s "carry forward" loop in state_store.py).
    for tag, events in events_by_player.items():
        update = apply_events(tag, events)
        print(
            f"[{tag}] new={len(update.new_uids)} changed={len(update.changed_uids)} "
            f"unchanged={len(update.unchanged_uids)} held_for_review={len(update.held_for_review)}"
        )
        if update.held_for_review:
            print(
                f"  -> {len(update.held_for_review)} event(s) held for review, see "
                f"{os.path.join('data', 'review_queue.json')} / tools/promote.py"
            )
        path = write_feed(tag, update.publishable, output_dir=feeds_dir)
        print(f"[{tag}] wrote {path} ({len(update.publishable)} event(s))")

    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-id", required=True, help="Any stable label for this post, e.g. manual-2026-06-29")
    parser.add_argument(
        "--text-file",
        help="Path to a file containing the transcribed post text. If omitted, reads from stdin.",
    )
    parser.add_argument("--feeds-dir", default=os.path.join(PROJECT_ROOT, "feeds"))
    args = parser.parse_args()

    if args.text_file:
        with open(args.text_file, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    try:
        sys.exit(run(text, args.post_id, args.feeds_dir))
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
