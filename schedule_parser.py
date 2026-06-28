"""
schedule_parser.py

Turns a raw post (plain text, already HTML-stripped) from
@草莓牛奶特别甜 into zero or more structured match events for
Wang Chuqin (王楚钦) and/or Sun Yingsha (孙颖莎).

Design notes (see design doc Section 3 for the full rationale):

- The confirmed source posts plain, consistently-formatted text for
  *upcoming* schedules, e.g.:

      WTT美国大满贯丨6月30日中国队赛程
      3:35 T1 覃予萱VS莎宾·温特
      4:45 T2 陈幸同VS刘叡潾
      5:20 T1 王楚钦/孙颖莎vsTBD
      9:00 T1 孙颖莎VS刘杨子

  This module's main job is parsing exactly that shape. Deterministic
  regex parsing is used rather than an LLM call, because the format is
  already this structured -- see README for when/why you might still
  want an LLM/vision fallback (image-based posts).

- The same account was also observed posting two OTHER shapes that are
  NOT schedules and must not be misparsed as one:

    1. "Result recap" posts: past-tense, set-by-set score lines --
       these describe a match that already happened, not an upcoming one.
    2. "Summary / links" posts: a header like "WTT美国大满贯丨赛前信息汇总"
       followed by lines pointing readers to a separate web link for the
       actual schedule -- the schedule itself is not inline text (often
       an image instead). These need a human (or a future linked-page
       fetcher) to follow up; this module deliberately does NOT try to
       guess content it cannot see.

  classify_post() exists specifically to keep these from being silently
  mis-extracted as schedule data.

Note on flag emoji: the real posts include a country-flag emoji (two
regional-indicator codepoints, e.g. the CN flag) directly after each
player's name with no space, e.g. "覃予萱<CN-flag>VS". This module strips
those out via _FLAG_RE.
"""

from __future__ import annotations

import re
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

# --- Known players -----------------------------------------------------

WANG_CHUQIN = "王楚钦"
SUN_YINGSHA = "孙颖莎"

# Other current national-team players seen in this account's posts, used
# only to raise extraction confidence when a name matches a known roster
# member (not a hard filter -- unrecognized names still parse, just at
# slightly lower confidence on the "player" field, since they could be a
# typo/OCR-style slip rather than a real opponent).
KNOWN_ROSTER = {
    WANG_CHUQIN, SUN_YINGSHA,
    "陈幸同", "覃予萱", "陈熠", "温瑞博", "王曼昱", "林诗栋", "梁靖崑", "陈梦",
}

TARGET_PLAYERS = {WANG_CHUQIN: "wangchuqin", SUN_YINGSHA: "sunyingsha"}

TIMEZONE_ASSUMED = "Asia/Shanghai"

# Regional indicator symbols used as flag emoji (each flag = 2 codepoints
# in this range). Stripped out of player names after we've used their
# presence as a (weak) signal that a name token ended.
_FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]")

_HEADER_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
_MATCH_LINE_RE = re.compile(
    r"^(?P<time>\d{1,2}:\d{2})\s*(?P<table>[A-Za-z]+\d+)\s*(?P<rest>.+)$"
)
_VS_SPLIT_RE = re.compile(r"[Vv][Ss]")
_RECAP_MARKER_RE = re.compile(r"第[一二三四五六七八九]盘[：:]")
_SCORE_RE = re.compile(r"\d{1,2}-\d{1,2}")
_LINK_MARKER_RE = re.compile(r"网页链接|微博正文")


@dataclass
class FieldValue:
    value: Optional[str]
    confidence: str  # "high" | "medium" | "low"


@dataclass
class ScheduleEvent:
    tournament_name: FieldValue
    date: FieldValue  # ISO yyyy-mm-dd, in the assumed timezone (see below)
    time_local: FieldValue  # "HH:MM"
    timezone_assumed: FieldValue
    table: FieldValue
    player1: FieldValue
    player2: FieldValue
    player_tags: list = field(default_factory=list)  # subset of {"wangchuqin","sunyingsha"}
    source_post_id: Optional[str] = None
    raw_line: str = ""


@dataclass
class ParseResult:
    post_classification: str  # "schedule" | "recap" | "summary_links" | "other"
    events: list = field(default_factory=list)  # list[ScheduleEvent], filtered to our two players
    notes: list = field(default_factory=list)  # human-readable flags, e.g. unresolved links


def classify_post(text: str) -> str:
    """Best-effort classification of a post's shape. See module docstring
    for what each category means and why it matters."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    has_match_line = any(_MATCH_LINE_RE.match(l) and "VS" in l.upper() for l in lines)
    if has_match_line:
        return "schedule"
    if _RECAP_MARKER_RE.search(text) and _SCORE_RE.search(text):
        return "recap"
    if _LINK_MARKER_RE.search(text):
        return "summary_links"
    return "other"


def _strip_flags(s: str) -> str:
    return _FLAG_RE.sub("", s).strip()


def _resolve_year(month: int, day: int, today: dt.date) -> int:
    """Posts only ever give month/day, never a year. Assume the current
    year, but roll forward to next year if that date would be more than
    ~60 days in the past relative to 'today' -- handles the
    December-into-January boundary without misdating things mid-year."""
    candidate = dt.date(today.year, month, day)
    if (today - candidate).days > 60:
        return today.year + 1
    return today.year


def _parse_header(header_line: str, today: dt.date):
    """Returns (tournament_name, date) field values from a header line like
    'WTT美国大满贯丨6月30日中国队赛程' (tournament-name + date)."""
    if "丨" in header_line:
        tournament_part, _, rest = header_line.partition("丨")
        tournament = FieldValue(tournament_part.strip() or None, "high" if tournament_part.strip() else "low")
    else:
        rest = header_line
        tournament = FieldValue(None, "low")

    m = _HEADER_DATE_RE.search(rest)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = _resolve_year(month, day, today)
        try:
            iso = dt.date(year, month, day).isoformat()
            date_field = FieldValue(iso, "high")
        except ValueError:
            date_field = FieldValue(None, "low")
    else:
        date_field = FieldValue(None, "low")

    return tournament, date_field


def _split_players(rest: str):
    parts = _VS_SPLIT_RE.split(rest, maxsplit=1)
    if len(parts) != 2:
        return rest.strip(), ""
    return parts[0].strip(), parts[1].strip()


def _player_field(raw_name: str) -> FieldValue:
    name = _strip_flags(raw_name)
    if name == "" or name == "TBD":
        return FieldValue(None if name == "" else "TBD", "low")
    # Doubles/mixed-doubles pairings are written like "Player1/Player2" --
    # keep the slash-joined form as the value, but confidence is based on
    # whether every component is recognized.
    components = [c.strip() for c in name.split("/") if c.strip()]
    if not components:
        return FieldValue(None, "low")
    if all(c in KNOWN_ROSTER for c in components):
        confidence = "high"
    else:
        confidence = "medium"
    return FieldValue(name, confidence)


def _matches_target_players(player_field: FieldValue) -> set:
    if not player_field.value:
        return set()
    tags = set()
    for full_name, tag in TARGET_PLAYERS.items():
        if full_name in player_field.value:
            tags.add(tag)
    return tags


def parse_post(text: str, source_post_id: Optional[str] = None, today: Optional[dt.date] = None) -> ParseResult:
    today = today or dt.date.today()
    classification = classify_post(text)

    if classification != "schedule":
        notes = []
        if classification == "summary_links":
            notes.append(
                "Post links out to a separate schedule page instead of inline text -- "
                "not auto-extractable; needs manual follow-up or a linked-page fetcher, "
                "which is out of scope for the MVP parser."
            )
        elif classification == "recap":
            notes.append("Post is a completed-match result recap, not an upcoming schedule -- skipped.")
        return ParseResult(post_classification=classification, events=[], notes=notes)

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    header_line = lines[0] if lines else ""
    tournament, date_field = _parse_header(header_line, today)

    events = []
    notes = []

    for line in lines[1:]:
        m = _MATCH_LINE_RE.match(line)
        if not m:
            continue  # not every line needs to be a match row (blank lines, asides, etc.)

        time_field = FieldValue(m.group("time"), "high")
        table_field = FieldValue(m.group("table"), "high")
        p1_raw, p2_raw = _split_players(m.group("rest"))
        p1_field = _player_field(p1_raw)
        p2_field = _player_field(p2_raw)

        tags = _matches_target_players(p1_field) | _matches_target_players(p2_field)
        if not tags:
            continue  # not a Wang Chuqin / Sun Yingsha match -- discard per design doc Section 2.3

        if date_field.value is None:
            notes.append(f"Could not resolve a date for line, skipping: {line!r}")
            continue

        events.append(
            ScheduleEvent(
                tournament_name=tournament,
                date=date_field,
                time_local=time_field,
                timezone_assumed=FieldValue(TIMEZONE_ASSUMED, "high"),
                table=table_field,
                player1=p1_field,
                player2=p2_field,
                player_tags=sorted(tags),
                source_post_id=source_post_id,
                raw_line=line,
            )
        )

    return ParseResult(post_classification="schedule", events=events, notes=notes)


def overall_confidence(event: ScheduleEvent) -> str:
    """Collapse per-field confidence into one publish/review decision.
    Matches design doc Section 3: only auto-publish when every field is
    high-confidence; anything else goes to manual review."""
    fields = [
        event.tournament_name, event.date, event.time_local,
        event.timezone_assumed, event.table, event.player1, event.player2,
    ]
    levels = {f.confidence for f in fields}
    if levels == {"high"}:
        return "high"
    if "low" in levels:
        return "low"
    return "medium"


def _run_fixture_checks():
    """Fixtures captured during development:
    (1) is the verified real schedule-post shape from the design doc.
    (2) and (3) are real posts observed live on the account that are NOT
        schedules, used here as negative tests for classify_post()."""
    schedule_fixture = (
        "WTT美国大满贯丨6月30日中国队赛程\n"
        "3:35 T1 覃予萱🇨🇳VS莎宾·温特🇩🇪\n"
        "4:45 T2 陈幸同🇨🇳VS刘叡潾🇰🇷\n"
        "5:20 T1 王楚钦/孙颖莎🇨🇳vsTBD\n"
        "9:00 T1 孙颖莎🇨🇳VS刘杨子🇦🇺\n"
        "9:35 T2 陈熠🇨🇳VS韩莹🇩🇪\n"
        "10:10 T2 温瑞博🇨🇳VS卢博米尔·扬察里克🇨🇿"
    )
    recap_fixture = (
        "2026亚洲青少年乒乓球锦标赛丨U19男团小组赛\n"
        "【新加坡2-3中国香港】\n"
        "第一盘：黎定龙3-1罗嘉杰\n"
        "【3-11，12-10，13-11，11-7】"
    )
    summary_links_fixture = (
        "WTT美国大满贯丨赛前信息汇总\n"
        "6月29日中国队赛程：网页链接\n"
        "6月30日中国队赛程：网页链接\n"
        "男单签表：网页链接女单签表：网页链接"
    )

    for name, fixture in [
        ("schedule", schedule_fixture),
        ("recap", recap_fixture),
        ("summary_links", summary_links_fixture),
    ]:
        result = parse_post(fixture, source_post_id=f"test-{name}", today=dt.date(2026, 6, 29))
        print(f"=== {name} -> classified as {result.post_classification} ===")
        for ev in result.events:
            print(
                f"  {ev.date.value} {ev.time_local.value} {ev.table.value} "
                f"{ev.player1.value} vs {ev.player2.value} tags={ev.player_tags} "
                f"overall_confidence={overall_confidence(ev)}"
            )
        for note in result.notes:
            print(f"  NOTE: {note}")


if __name__ == "__main__":
    _run_fixture_checks()
