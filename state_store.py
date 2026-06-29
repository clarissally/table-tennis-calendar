"""
state_store.py

Persistent state + diffing layer that sits between schedule_parser.py and
ics_generator.py (see design doc Section 2.1 pipeline diagram, and the
"Postponements/changes" note in Section 2.3 / row in the Section 6 risk
table).

Why this exists: every pipeline run only sees a handful of *recent* posts,
not a full canonical schedule. If a match gets re-announced with a changed
time/table, naively regenerating the feed from scratch would either create
a duplicate event or silently lose the calendar client's existing copy.
The fix is what RFC5545 is built for: give every distinct match a stable
UID and bump SEQUENCE/LAST-MODIFIED when any of its fields change, instead
of deleting and recreating. This module owns that bookkeeping.

Two on-disk stores, both plain JSON (no DB dependency, matches the
project's "small JSON/SQLite file" suggestion in the design doc -- JSON
was chosen here for zero dependencies and easy manual inspection/debugging):

  data/state_<player_tag>.json   -- one per feed (wangchuqin / sunyingsha).
                                     Maps UID -> StoredEvent for every event
                                     ever seen for that player, including
                                     held (non-high-confidence) ones, so a
                                     later post that fills in the missing
                                     detail can find and upgrade the same
                                     UID instead of creating a second entry.

  data/review_queue.json         -- flat list of ReviewItem entries for any
                                     event whose overall_confidence is not
                                     "high". A human (per the user's
                                     decision to run near-fully-automated,
                                     this should stay small) looks at this
                                     periodically; see README "Manual
                                     review" for the workflow.

Known limitation (intentional, MVP scope): this module never deletes an
event on its own. The source account does not post a single canonical
"this is the full current schedule" feed we could diff against for
removals -- only incremental announcements -- so we cannot safely
distinguish "this match was cancelled" from "this match just wasn't
mentioned in the last few posts we happened to see". Cancellations/
postponements therefore rely on the account posting an explicit update
(which *does* flow through normally, since it re-states date/time/table
and we match it to the same UID) or on manual removal. See design doc
Section 8 (next steps) re: revisiting this once real-world accuracy data
exists.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from schedule_parser import ScheduleEvent, overall_confidence

UID_DOMAIN = "table-tennis-calendar.example"  # placeholder -- see README, replace with your real deployment domain

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REVIEW_QUEUE_PATH = os.path.join(DATA_DIR, "review_queue.json")


def _state_path(player_tag: str) -> str:
    return os.path.join(DATA_DIR, f"state_{player_tag}.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class StoredEvent:
    uid: str
    tournament_name: Optional[str]
    date: Optional[str]
    time_local: Optional[str]
    timezone_assumed: Optional[str]
    table: Optional[str]
    player1: Optional[str]
    player2: Optional[str]
    player_tags: List[str]
    confidence: str  # overall_confidence() result at time of last update
    sequence: int
    last_modified: str  # ISO8601 UTC
    source_post_id: Optional[str] = None
    raw_line: str = ""


@dataclass
class ReviewItem:
    uid: str
    reason: str
    queued_at: str
    event: dict  # asdict(StoredEvent), so a reviewer can see the full proposed event


@dataclass
class UpdateResult:
    new_uids: List[str] = field(default_factory=list)
    changed_uids: List[str] = field(default_factory=list)
    unchanged_uids: List[str] = field(default_factory=list)
    held_for_review: List[str] = field(default_factory=list)  # uids not auto-published this run
    publishable: List[StoredEvent] = field(default_factory=list)  # high-confidence events to render to ICS


def compute_uid(event: ScheduleEvent) -> str:
    """Stable UID derived from (date, table, the two players) -- NOT from
    source_post_id, since the same match gets re-announced across multiple
    posts and must collapse to the same UID each time. Table number is
    included because it's part of how the source distinguishes concurrent
    matches at the same time slot; if a correction changes the table for
    the same date+players, see the note in apply_update() about why that's
    treated as a field change on the same UID rather than a new identity --
    we deliberately do NOT include table in a way that would split the UID,
    since a few real bug accounts confirmed bumping table is the same
    match. See _identity_key().
    """
    key = _identity_key(event)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"ttcal-{digest}@{UID_DOMAIN}"


def _identity_key(event: ScheduleEvent) -> str:
    # Sort the two player values so "A vs B" and "B vs A" (shouldn't happen
    # from one source, but cheap insurance) hash identically.
    players = sorted([event.player1.value or "", event.player2.value or ""])
    return "|".join([event.date.value or "", players[0], players[1]])


def _to_stored(event: ScheduleEvent, uid: str, sequence: int, last_modified: str) -> StoredEvent:
    return StoredEvent(
        uid=uid,
        tournament_name=event.tournament_name.value,
        date=event.date.value,
        time_local=event.time_local.value,
        timezone_assumed=event.timezone_assumed.value,
        table=event.table.value,
        player1=event.player1.value,
        player2=event.player2.value,
        player_tags=list(event.player_tags),
        confidence=overall_confidence(event),
        sequence=sequence,
        last_modified=last_modified,
        source_post_id=event.source_post_id,
        raw_line=event.raw_line,
    )


def _fields_equal(a: StoredEvent, b_event: ScheduleEvent) -> bool:
    return (
        a.tournament_name == b_event.tournament_name.value
        and a.date == b_event.date.value
        and a.time_local == b_event.time_local.value
        and a.timezone_assumed == b_event.timezone_assumed.value
        and a.table == b_event.table.value
        and a.player1 == b_event.player1.value
        and a.player2 == b_event.player2.value
    )


def load_state(player_tag: str) -> Dict[str, StoredEvent]:
    path = _state_path(player_tag)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {uid: StoredEvent(**fields) for uid, fields in raw.items()}


def save_state(player_tag: str, state: Dict[str, StoredEvent]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _state_path(player_tag)
    serializable = {uid: asdict(ev) for uid, ev in state.items()}
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)  # atomic on POSIX -- avoids a half-written state file if interrupted


def load_review_queue() -> List[ReviewItem]:
    if not os.path.exists(REVIEW_QUEUE_PATH):
        return []
    with open(REVIEW_QUEUE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [ReviewItem(**item) for item in raw]


def save_review_queue(items: List[ReviewItem]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = REVIEW_QUEUE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump([asdict(item) for item in items], f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, REVIEW_QUEUE_PATH)


def apply_events(player_tag: str, events: List[ScheduleEvent]) -> UpdateResult:
    """Diff a fresh batch of parsed events (already filtered to ones tagged
    with this player) against the on-disk state for that player, updating
    the state file and the shared review queue in place, and returning a
    summary including the list of StoredEvent that should be (re)rendered
    into this player's ICS feed this run.

    Confidence gating (per the user's "near-fully-automated" decision):
    only overall_confidence == "high" auto-publishes. "medium"/"low"
    events are written into the review queue instead and are NOT included
    in `publishable` -- they will not appear on the calendar until a human
    promotes them (or a later, more complete post raises their confidence
    and they pass through automatically on a subsequent run).
    """
    state = load_state(player_tag)
    review_queue = load_review_queue()
    review_by_uid = {item.uid: item for item in review_queue}

    result = UpdateResult()
    now = _now_iso()

    for event in events:
        if player_tag not in event.player_tags:
            continue  # defensive -- caller should already have filtered to this player

        uid = compute_uid(event)
        confidence = overall_confidence(event)

        existing = state.get(uid)
        if existing is None:
            stored = _to_stored(event, uid, sequence=0, last_modified=now)
            result.new_uids.append(uid)
        elif not _fields_equal(existing, event):
            stored = _to_stored(event, uid, sequence=existing.sequence + 1, last_modified=now)
            result.changed_uids.append(uid)
        else:
            # No field changed. Still refresh confidence/source bookkeeping
            # in case this is the post that finally resolves a "medium"
            # event to "high", without bumping SEQUENCE (nothing calendar-
            # visible changed).
            #
            # BUT: do not let this silently *downgrade* an event that is
            # already "high" -- whether the parser itself decided that, or
            # a human promoted it via tools/promote.py after checking the
            # source post. Without this guard, a manually-approved event
            # gets demoted right back into the review queue on the very
            # next run, as soon as the same (still-recent, still-unchanged)
            # source post is re-fetched and re-parsed to the same "medium"
            # confidence it always had -- which defeats the entire point of
            # promote.py. Confidence is allowed to move up on its own
            # (medium -> high, e.g. a clarifying repost) but never down
            # once a human (or the parser) has called it high.
            stored = existing
            if confidence == "high" or existing.confidence != "high":
                stored.confidence = confidence
            stored.source_post_id = event.source_post_id
            stored.raw_line = event.raw_line
            result.unchanged_uids.append(uid)

        state[uid] = stored

        if stored.confidence == "high":
            result.publishable.append(stored)
            review_by_uid.pop(uid, None)  # if it was previously held, it's resolved now
        else:
            result.held_for_review.append(uid)
            review_by_uid[uid] = ReviewItem(
                uid=uid,
                reason=f"overall_confidence={confidence}; needs human check before publishing",
                queued_at=review_by_uid.get(uid, ReviewItem(uid, "", now, {})).queued_at if uid in review_by_uid else now,
                event=asdict(stored),
            )

    # Include any previously-published high-confidence events that simply
    # weren't mentioned in this batch -- they stay on the calendar (see
    # module docstring "Known limitation" re: no auto-removal).
    seen_uids = {compute_uid(e) for e in events if player_tag in e.player_tags}
    for uid, stored in state.items():
        if uid not in seen_uids and stored.confidence == "high":
            result.publishable.append(stored)

    save_state(player_tag, state)
    save_review_queue(list(review_by_uid.values()))

    return result


if __name__ == "__main__":
    # Minimal smoke test using schedule_parser's own fixtures, run twice to
    # exercise both the "new" and "unchanged" code paths.
    import datetime as dt
    from schedule_parser import parse_post

    fixture = (
        "WTT美国大满贯丨6月30日中国队赛程\n"
        "5:20 T1 王楚钦/孙颖莎🇨🇳vsTBD\n"
        "9:00 T1 孙颖莎🇨🇳VS刘杨子🇦🇺\n"
    )
    parsed = parse_post(fixture, source_post_id="smoketest-1", today=dt.date(2026, 6, 29))

    for tag in ("wangchuqin", "sunyingsha"):
        tagged_events = [e for e in parsed.events if tag in e.player_tags]
        r1 = apply_events(tag, tagged_events)
        print(f"[{tag}] run 1: new={r1.new_uids} held={r1.held_for_review} publishable={[s.uid for s in r1.publishable]}")
        r2 = apply_events(tag, tagged_events)
        print(f"[{tag}] run 2: unchanged={r2.unchanged_uids} new={r2.new_uids}")
