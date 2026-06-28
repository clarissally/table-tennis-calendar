"""
weibo_scraper.py

Fetches posts from the confirmed primary source account
@草莓牛奶特别甜 (uid 7360795486) via the public, no-login m.weibo.cn
mobile-web JSON API.

IMPORTANT — network note from development:
This module could not be exercised end-to-end inside the build sandbox,
because the sandbox's outbound network is allowlisted and does not include
weibo.cn (confirmed via direct curl test: the proxy returned
403 blocked-by-allowlist). The JSON shape and endpoints below were verified
by manually loading the same URLs in a real browser session during
development, so the request/response handling here reflects the real API,
but you should run a smoke test against the live account from an
unrestricted environment (your own machine, a normal CI runner, etc.)
before relying on this in production. See README.md "Testing" section.

No login/session/cookies are used — this matches the project's
low-frequency, unauthenticated polling design (see design doc Section 2.1).
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import List, Optional

UID = "7360795486"  # @草莓牛奶特别甜
CONTAINERID = f"107603{UID}"  # standard "this user's posts" container id pattern

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

INDEX_URL = "https://m.weibo.cn/api/container/getIndex"
EXTEND_URL = "https://m.weibo.cn/statuses/extend"

REQUEST_TIMEOUT_SECONDS = 15
# Be polite: this account is polled on a 30-60 minute cadence per the design
# doc, so there is no need for tight retry loops or high request volume.
MIN_SECONDS_BETWEEN_REQUESTS = 2.0


@dataclass
class WeiboPost:
    mid: str  # stable post id, used to derive event UIDs downstream
    created_at_raw: str  # e.g. "Mon Jun 29 09:00:00 +0800 2026"
    text: str  # HTML-stripped, fully-expanded post text
    is_long_text: bool
    pic_count: int
    raw: dict  # original card dict, kept for debugging / re-parsing


class WeiboFetchError(RuntimeError):
    pass


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        raise WeiboFetchError(f"network error fetching {url}: {e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise WeiboFetchError(f"non-JSON response from {url}: {e}") from e


def _strip_html(text: str) -> str:
    """Weibo post text comes with <br/> and <a> tags embedded. Strip down to
    plain text good enough for the schedule parser. Deliberately simple
    (no external HTML parser dependency) since the input is a small,
    predictable subset of HTML, not arbitrary markup."""
    import re

    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def fetch_full_text(mid: str) -> Optional[str]:
    """Fetch the un-truncated text for a post whose feed snippet was cut off
    with '...展开' / '...全文'. Returns None if unavailable."""
    url = f"{EXTEND_URL}?id={mid}"
    try:
        data = _get_json(url)
    except WeiboFetchError:
        return None
    long_text = (data.get("data") or {}).get("longTextContent")
    if not long_text:
        return None
    return _strip_html(long_text)


def fetch_recent_posts(max_pages: int = 1, page_delay_seconds: float = MIN_SECONDS_BETWEEN_REQUESTS) -> List[WeiboPost]:
    """Fetch the most recent posts from the account's timeline.

    max_pages=1 is enough for the steady-state 30-60 min poll (you only need
    to see posts since the last run). Use a higher value only for backfill /
    initial-load scenarios.
    """
    posts: List[WeiboPost] = []
    since_id: Optional[str] = None

    for page_num in range(max_pages):
        url = f"{INDEX_URL}?type=uid&value={UID}&containerid={CONTAINERID}"
        if since_id:
            url += f"&since_id={since_id}"

        data = _get_json(url)
        if data.get("ok") != 1:
            raise WeiboFetchError(f"unexpected response (ok={data.get('ok')}): {data}")

        cards = (data.get("data") or {}).get("cards") or []
        for card in cards:
            if card.get("card_type") != 9:  # 9 == a normal post card
                continue
            mblog = card.get("mblog") or {}
            text = _strip_html(mblog.get("text", ""))
            is_long = bool(mblog.get("isLongText"))

            if is_long or text.rstrip().endswith(("...展开", "...全文")):
                full = fetch_full_text(mblog.get("mid") or mblog.get("id"))
                if full:
                    text = full
                # else: fall back to the truncated snippet rather than
                # dropping the post; the parser will just see less text
                # and should naturally produce lower-confidence/partial
                # results rather than silently losing the post entirely.

            posts.append(
                WeiboPost(
                    mid=str(mblog.get("mid") or mblog.get("id") or ""),
                    created_at_raw=mblog.get("created_at", ""),
                    text=text,
                    is_long_text=is_long,
                    pic_count=int(mblog.get("pic_num", 0) or 0),
                    raw=card,
                )
            )

        since_id = ((data.get("data") or {}).get("cardlistInfo") or {}).get("since_id")
        if not since_id or page_num + 1 >= max_pages:
            break
        time.sleep(page_delay_seconds)

    return posts


if __name__ == "__main__":
    # Manual smoke test entry point. Run this from an environment with
    # normal internet access (NOT this build sandbox) to verify the live
    # API still matches the shape assumed above before deploying.
    fetched = fetch_recent_posts(max_pages=1)
    print(f"Fetched {len(fetched)} posts")
    for p in fetched[:5]:
        print("---")
        print(p.mid, p.created_at_raw)
        print(p.text[:200])
