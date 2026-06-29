"""
tools/migrate_state_uids.py

One-time migration for two UID-scheme changes that both make existing
data/state_<tag>.json entries fail to match against freshly-computed UIDs
on the next pipeline run (which would silently duplicate every event,
since state_store.py never auto-deletes -- see its module docstring):

  1. UID_DOMAIN was changed from the placeholder "table-tennis-calendar.example"
     to the real deployed domain "clarissally.github.io" (task: "Replace
     placeholder domain with real Pages URL"), but the already-written
     state files were never re-keyed, so every stored UID still ends in
     the old domain.

  2. _identity_key() was changed to drop the opponent name from the
     identity hash (see state_store.py's docstring for the bug this
     fixes: an opponent edit, e.g. "vs TBD" -> "vs <real name>", used to
     hash to a brand-new UID instead of updating the existing event).

Both changes mean: compute_uid() today produces a different string than
what's stored as the dict key for every event recorded before this fix.
This script re-keys each entry under today's compute_uid() logic, in
place, preserving sequence/last_modified/everything else -- so the next
pipeline run recognizes them as already-seen instead of creating
duplicates.

Run once:
    python3 tools/migrate_state_uids.py
Then regenerate the feeds so the .ics files pick up the new UIDs too:
    python3 tools/regen_feeds.py
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from schedule_parser import TARGET_PLAYERS, FieldValue, ScheduleEvent  # noqa: E402
from state_store import compute_uid, load_state, save_state  # noqa: E402


def _stored_to_schedule_event(stored) -> ScheduleEvent:
    """Rebuild just enough of a ScheduleEvent for compute_uid() to work on
    a StoredEvent loaded from disk (StoredEvent keeps plain strings, not
    FieldValue-wrapped ones)."""
    return ScheduleEvent(
        tournament_name=FieldValue(stored.tournament_name, "high"),
        date=FieldValue(stored.date, "high"),
        time_local=FieldValue(stored.time_local, "high"),
        timezone_assumed=FieldValue(stored.timezone_assumed, "high"),
        table=FieldValue(stored.table, "high"),
        player1=FieldValue(stored.player1, "high"),
        player2=FieldValue(stored.player2, "high"),
        player_tags=list(stored.player_tags),
    )


def main():
    for tag in TARGET_PLAYERS.values():
        state = load_state(tag)
        if not state:
            print(f"[{tag}] no state file, nothing to migrate")
            continue

        new_state = {}
        renamed = 0
        collisions = 0
        for old_uid, stored in state.items():
            new_uid = compute_uid(_stored_to_schedule_event(stored))
            if new_uid != old_uid:
                renamed += 1
            if new_uid in new_state:
                collisions += 1
                print(
                    f"[{tag}] WARNING: {old_uid!r} and an earlier entry both "
                    f"map to new uid {new_uid!r} -- one will overwrite the "
                    f"other. Inspect manually before trusting this run."
                )
            stored.uid = new_uid
            new_state[new_uid] = stored

        save_state(tag, new_state)
        print(
            f"[{tag}] migrated {len(state)} event(s): "
            f"{renamed} re-keyed, {len(state) - renamed} unchanged, "
            f"{collisions} collision(s)"
        )


if __name__ == "__main__":
    main()
