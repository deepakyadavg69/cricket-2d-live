"""
scraper.py — Cricbuzz live data ingestion.

WHY THIS LOOKS DIFFERENT FROM OLD TUTORIALS
-------------------------------------------
Every Cricbuzz tutorial you'll find uses one of these:

    http://mapps.cricbuzz.com/cbzios/match/livematches          -> DEAD (no response)
    https://www.cricbuzz.com/api/cricket-match/livematches      -> 404
    https://www.cricbuzz.com/api/cricket-match/commentary/<id>  -> 404

Cricbuzz is now a Next.js App-Router site. It no longer exposes a public JSON
API. Instead the server-rendered HTML ships its data as a React Server
Component (RSC) stream inside script tags that look like:

    <script>self.__next_f.push([1,"{\\"matchId\\":174141, ...}"])</script>

So the correct (and still free / keyless) approach is:
    1. GET the ordinary HTML page with a browser User-Agent
    2. Reassemble every `self.__next_f.push([1,"..."])` chunk into one string
    3. Brace-match the JSON objects we need out of it

Two blobs matter to us:

  "miniscore"        (from /live-cricket-scores/<id>/<slug>)
      inningsId, batTeam{teamScore,teamWkts}, overs, target,
      batsmanStriker{id,name,runs,balls,fours,sixes,strikeRate},
      batsmanNonStriker{...},
      bowlerStriker{id,name,overs,maidens,runs,wickets,economy},
      currentRunRate, requiredRunRate, remRunsToWin, oversRem,
      partnerShip, recentOvsStats ("... 0 0  | 1 4 0 6 W 2"), lastWicket,
      matchScoreDetails, status, event, responseLastUpdated

  "matchPreviewFullComm"  (from /live-cricket-full-commentary/<id>/<slug>)
      commentary[].commentaryList[] -> commText, event, ballNbr, timestamp,
      batsmanStriker{}, bowlerStriker{}, totalRuns, legalRuns, batTeamScore

No API key. No proxy. No headless browser. Pure HTML + brace matching.

RESILIENCE
----------
Live sports sites change constantly, so this scraper never trusts one field.
Everything is read defensively, every network call is retried with backoff, and
`python probe.py` re-verifies the endpoints against the live site in ~2 seconds
so you can see exactly what broke if Cricbuzz ships a redesign.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Optional

import aiohttp

LOG = logging.getLogger("scraper")

CRICBUZZ = "https://www.cricbuzz.com"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": CRICBUZZ + "/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Ball tokens we understand in `recentOvsStats`
VALID_BALL_TOKENS = {"0", "1", "2", "3", "4", "5", "6", "W", "wd", "nb", "lb", "b"}

# Exact team objects found in the RSC payload:
#   {"teamId": 2, "teamName": "India", "teamSName": "IND", "imageId": 776162}
TEAM_RE = re.compile(
    r'\{\s*"teamId"\s*:\s*(\d+)\s*,\s*"teamName"\s*:\s*"([^"]+)"\s*,\s*"teamSName"\s*:\s*"([^"]+)"'
)
GROUND_RE = re.compile(r'"ground"\s*:\s*"([^"]{2,60})"')
CITY_RE = re.compile(r'"city"\s*:\s*"([^"]{2,40})"')
STATE_RE = re.compile(r'"state"\s*:\s*"(In Progress|Toss|Complete|Result|Preview|Upcoming|Rain|Abandoned)"')


# ---------------------------------------------------------------------------
#  RSC (React Server Component) payload decoding
# ---------------------------------------------------------------------------
_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)


def _decode_chunk(raw: str) -> str:
    """Undo the JS string escaping used inside the RSC script tags."""
    try:
        return json.loads('"' + raw.replace("\n", "\\n") + '"')
    except Exception:
        try:
            return raw.encode("utf-8", "surrogatepass").decode("unicode_escape", "replace")
        except Exception:
            return raw


def rsc_payload(html: str) -> str:
    """Reassemble all RSC chunks from an HTML page into one searchable string."""
    return "".join(_decode_chunk(c) for c in _RSC_CHUNK.findall(html))


def extract_object(buf: str, key: str, last: bool = True) -> Optional[dict]:
    """
    Find `"key": { ... }` inside the RSC buffer and return the parsed object.
    Uses brace matching so nested objects survive. `last=True` takes the final
    occurrence (the most recently streamed / most complete one).
    """
    needle = '"%s"' % key
    positions = [m.start() for m in re.finditer(re.escape(needle), buf)]
    if not positions:
        return None
    positions = reversed(positions) if last else positions
    for pos in positions:
        brace = buf.find("{", pos)
        if brace < 0:
            continue
        depth, i, n = 0, brace, len(buf)
        while i < n:
            ch = buf[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(buf[brace : i + 1])
                    except Exception:
                        break
            i += 1
    return None


def _enclosing_start(buf: str, pos: int) -> int:
    """Walk backwards to the `{` that opens the innermost object containing pos."""
    depth, j = 0, pos
    while j > 0:
        c = buf[j]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                return j
            depth -= 1
        j -= 1
    return -1


def extract_enclosing(buf: str, pattern: str, many: bool = False) -> list:
    """
    Match `pattern` anywhere in the RSC buffer, then return the *enclosing*
    JSON object. This is far more reliable than guessing where an object
    starts, which is what breaks most naive Cricbuzz scrapers.
    """
    out = []
    for m in re.finditer(pattern, buf):
        start = _enclosing_start(buf, m.start())
        if start < 0:
            continue
        depth, i, n = 0, start, len(buf)
        while i < n:
            if buf[i] == "{":
                depth += 1
            elif buf[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        out.append(json.loads(buf[start : i + 1]))
                    except Exception:
                        pass
                    break
            i += 1
        if not many and out:
            break
    return out


def extract_teams(html: str) -> dict:
    """teamId -> {name, sname}. e.g. 2 -> {'name': 'India', 'sname': 'IND'}"""
    teams = {}
    for tid, name, sname in TEAM_RE.findall(html):
        teams[int(tid)] = {"name": name, "sname": sname}
    return teams


def extract_header(html: str, match_id: str = "") -> dict:
    """
    Match-level metadata: teams, series, format, venue, state.
    The <h1> is the single most stable string on the page:
        'South Africa Champions vs West Indies Champions, 3rd Match, WCL 2026'

    `match_id` matters: the RSC payload of /live-cricket-scores carries the
    whole scoreboard, so without filtering by id we'd happily read the
    *featured* match's series/format instead of the one we're streaming.
    """
    h1 = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if m:
        h1 = re.sub(r"<[^>]+>", "", m.group(1))
        h1 = re.sub(r"\s+", " ", h1).strip()
        h1 = h1.replace("&amp;", "&").replace("&nbsp;", " ")

    buf = rsc_payload(html)
    head, fallback = {}, {}
    for o in extract_enclosing(buf, r'"state"\s*:\s*"', many=True):
        if "matchId" not in o:
            continue
        if match_id and str(o.get("matchId")) != str(match_id):
            continue
        if "seriesName" in o or "matchFormat" in o:
            head = o
            break
    if not head:
        for o in extract_enclosing(buf, r'"matchFormat"\s*:\s*"', many=True):
            if match_id and str(o.get("matchId")) != str(match_id):
                continue
            head = o
            break

    gm = GROUND_RE.search(buf) or GROUND_RE.search(html)
    cm = CITY_RE.search(buf) or CITY_RE.search(html)
    return {
        "h1": h1,
        "match_desc": head.get("matchDesc") or "",
        "format": head.get("matchFormat") or "",
        "series": head.get("seriesName") or "",
        "state": head.get("state") or "",
        "short_status": head.get("shortStatus") or "",
        "curr_bat_team_id": inum(head.get("currBatTeamId")),
        "start_date": head.get("startDate") or "",
        "ground": gm.group(1) if gm else "",
        "city": cm.group(1) if cm else "",
    }


# ---------------------------------------------------------------------------
#  Safe accessors — Cricbuzz renames/omits fields without warning
# ---------------------------------------------------------------------------
def g(d: Any, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
        if d is None:
            return default
    return d


def fnum(v, default=0.0) -> float:
    try:
        if v in (None, "", "-"):
            return default
        return float(str(v).replace("%", "").strip())
    except Exception:
        return default


def inum(v, default=0) -> int:
    try:
        if v in (None, "", "-"):
            return default
        return int(fnum(v, default))
    except Exception:
        return default


def overs_to_balls(overs: Any) -> int:
    """'16.2' -> 98 legal balls."""
    try:
        s = str(overs)
        if "." in s:
            whole, frac = s.split(".", 1)
            return int(whole) * 6 + int(frac)
        return int(s) * 6
    except Exception:
        return 0


def balls_to_overs(balls: int) -> str:
    return "%d.%d" % (balls // 6, balls % 6)


def _initials(name: str) -> str:
    """'South Africa Champions' -> 'SAC' (fallback short code)."""
    parts = [p for p in re.split(r"[\s\-]+", name or "") if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][:4].upper()
    return "".join(p[0] for p in parts[:3]).upper()


def parse_recent_overs(recent: str) -> tuple:
    """
    recentOvsStats looks like:  '... 0 0  | 1 4 0 6 W 2  | 0 1'
    Returns (this_over_tokens, previous_over_tokens)
    """
    if not recent:
        return [], []
    parts = [p.strip() for p in str(recent).split("|")]
    parts = [p for p in parts if p]
    tokens = lambda p: [t for t in p.replace("...", " ").split() if t in VALID_BALL_TOKENS]
    this_over = tokens(parts[-1]) if parts else []
    prev_over = tokens(parts[-2]) if len(parts) > 1 else []
    return this_over, prev_over


# ---------------------------------------------------------------------------
#  Scraper
# ---------------------------------------------------------------------------
class CricbuzzScraper:
    def __init__(
        self,
        match_id: Optional[str] = None,
        interval: float = 5.0,
        auto_pick: bool = True,
        series_filter: str = "",
    ):
        self.match_id = str(match_id) if match_id else None
        self.interval = interval
        self.auto_pick = auto_pick
        self.series_filter = series_filter.lower()
        self.session: Optional[aiohttp.ClientSession] = None

        self.slug: str = "live"
        self.meta: dict = {}
        self.last_ok: float = 0.0
        self.errors = 0
        self.status = "starting"

    # -- lifecycle ----------------------------------------------------------
    async def start(self):
        timeout = aiohttp.ClientTimeout(total=25, connect=10)
        self.session = aiohttp.ClientSession(
            headers=HEADERS, timeout=timeout, raise_for_status=False
        )
        return self

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # -- HTTP ---------------------------------------------------------------
    async def _get(self, url: str) -> str:
        for attempt in range(3):
            try:
                async with self.session.get(url) as r:
                    if r.status == 200:
                        self.last_ok = time.time()
                        self.errors = 0
                        self.status = "ok"
                        return await r.text(errors="ignore")
                    LOG.warning("HTTP %s for %s", r.status, url)
                    self.status = "http_%s" % r.status
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("fetch error (%s): %s", url, exc)
                self.status = "error"
            await asyncio.sleep(1.5 * (attempt + 1))
        self.errors += 1
        return ""

    # -- match discovery ----------------------------------------------------
    async def list_matches(self) -> list:
        """All matches on the live-scores board, newest/live first."""
        html = await self._get(CRICBUZZ + "/cricket-match/live-scores")
        if not html:
            return []
        buf = rsc_payload(html)
        teams = extract_teams(html)

        seen, out = set(), []
        for obj in extract_enclosing(buf, r'"state"\s*:\s*"', many=True):
            mid = str(obj.get("matchId") or "")
            if not mid.isdigit() or mid in seen:
                continue
            seen.add(mid)
            # the listing page names teams in <a> tags near the match id
            t1 = (teams.get(inum(obj.get("team1Id")), {}) or {}).get("name", "")
            t2 = (teams.get(inum(obj.get("team2Id")), {}) or {}).get("name", "")
            if not t1 or not t2:
                slugs = re.findall(
                    r"/live-cricket-scores/%s/([a-z0-9\-]+)" % mid, html
                )
                guess = slugs[0].split("-vs-") if slugs else []
                t1 = t1 or (guess[0].replace("-", " ").title() if guess else "")
                t2 = t2 or (guess[1].split("-")[0].replace("-", " ").title() if len(guess) > 1 else "")
            out.append(
                {
                    "match_id": mid,
                    "series": obj.get("seriesName") or "",
                    "desc": obj.get("matchDesc") or "",
                    "format": obj.get("matchFormat") or "",
                    "state": obj.get("state") or "",
                    "status": obj.get("shortStatus") or obj.get("status") or "",
                    "team1": t1,
                    "team2": t2,
                    "batting_team_id": inum(obj.get("currBatTeamId")),
                }
            )
        return out

    async def _auto_pick(self) -> Optional[str]:
        matches = await self.list_matches()
        live = [m for m in matches if m["state"].lower() in ("in progress", "toss")]
        if self.series_filter:
            live = [m for m in live if self.series_filter in m["series"].lower()]
        if live:
            pick = live[0]
            LOG.info("Auto-picked live match %s: %s vs %s", pick["match_id"], pick["team1"], pick["team2"])
            return pick["match_id"]
        # nothing live right now -> fall back to the most recent completed match
        # so the overlay always has something real to render while you rehearse.
        done = [m for m in matches if m["state"].lower() in ("complete", "result")]
        if done:
            pick = done[0]
            LOG.info("No live match. Rehearsing on latest completed: %s vs %s", pick["team1"], pick["team2"])
            return pick["match_id"]
        return None

    async def resolve_match(self) -> bool:
        if not self.match_id or self.match_id in ("auto", "0", "None"):
            self.match_id = await self._auto_pick()
        if not self.match_id:
            self.status = "no_match"
            return False
        return True

    def set_match(self, match_id: str):
        self.match_id = str(match_id)
        self.slug = "live"
        self.meta = {}

    # -- the actual fetch ---------------------------------------------------
    async def snapshot(self) -> Optional[dict]:
        """
        One combined snapshot: live score + players + recent commentary text.
        Returns None when the fetch/parse failed entirely.
        """
        if not self.slug or self.slug == "live":
            url = "%s/live-cricket-scores/%s/live" % (CRICBUZZ, self.match_id)
        else:
            url = "%s/live-cricket-scores/%s/%s" % (CRICBUZZ, self.match_id, self.slug)

        html = await self._get(url)
        if not html:
            return None

        buf = rsc_payload(html)
        mini = extract_object(buf, "miniscore")
        if not mini:
            LOG.warning("miniscore not found in RSC payload (site redesign?)")
            self.status = "parse_error"
            return None

        # update slug from canonical link so later requests are cache-friendly
        m = re.search(r"/live-cricket-scores/%s/([a-z0-9\-]+)" % self.match_id, html)
        if m:
            self.slug = m.group(1)

        comm = extract_object(buf, "matchCommentary") or {}
        lines = []
        for c in g(comm, "commentaryList", default=[]) or []:
            txt = (g(c, "commText", default="") or "").strip()
            if txt:
                lines.append(
                    {
                        "text": txt,
                        "over": fnum(g(c, "overNumber", default=0)),
                        "event": g(c, "event", default="NONE") or "NONE",
                        "timestamp": inum(g(c, "timestamp", default=0)),
                    }
                )
        # the RSC sometimes only carries the *preview* commentary; if so, hit
        # the dedicated full-commentary page for the real ball-by-ball list.
        if not lines:
            lines = await self.ball_by_ball()

        this_over, prev_over = parse_recent_overs(g(mini, "recentOvsStats", default="") or "")

        bs = g(mini, "batsmanStriker", default={}) or {}
        bns = g(mini, "batsmanNonStriker", default={}) or {}
        bw = g(mini, "bowlerStriker", default={}) or {}
        bt = g(mini, "batTeam", default={}) or {}

        snap = {
            "match_id": self.match_id,
            "fetched_at": time.time(),
            "status": g(mini, "status", default="") or "",
            "state": "live",
            "innings_id": inum(g(mini, "inningsId", default=1), 1),
            "bat_team_id": inum(g(bt, "teamId", default=0)),
            "score": inum(g(bt, "teamScore", default=0)),
            "wickets": inum(g(bt, "teamWkts", default=0)),
            "overs": str(g(mini, "overs", default="0.0") or "0.0"),
            "balls": overs_to_balls(g(mini, "overs", default="0.0")),
            "target": inum(g(mini, "target", default=0)),
            "crr": fnum(g(mini, "currentRunRate", default=0)),
            "rrr": fnum(g(mini, "requiredRunRate", default=0)),
            "need": inum(g(mini, "remRunsToWin", default=0)),
            "balls_left": inum(g(mini, "oversRem", default=0)),
            "partnership": inum(g(mini, "partnerShip", default=0)),
            "last_wicket": g(mini, "lastWicket", default="") or "",
            "recent_ovs": g(mini, "recentOvsStats", default="") or "",
            "this_over": this_over,
            "prev_over": prev_over,
            "striker": {
                "id": inum(g(bs, "id", default=0)),
                "name": (g(bs, "name", default="") or "").upper(),
                "runs": inum(g(bs, "runs", default=0)),
                "balls": inum(g(bs, "balls", default=0)),
                "fours": inum(g(bs, "fours", default=0)),
                "sixes": inum(g(bs, "sixes", default=0)),
                "sr": fnum(g(bs, "strikeRate", default=0)),
            },
            "non_striker": {
                "id": inum(g(bns, "id", default=0)),
                "name": (g(bns, "name", default="") or "").upper(),
                "runs": inum(g(bns, "runs", default=0)),
                "balls": inum(g(bns, "balls", default=0)),
                "fours": inum(g(bns, "fours", default=0)),
                "sixes": inum(g(bns, "sixes", default=0)),
                "sr": fnum(g(bns, "strikeRate", default=0)),
            },
            "bowler": {
                "id": inum(g(bw, "id", default=0)),
                "name": (g(bw, "name", default="") or "").upper(),
                "overs": fnum(g(bw, "overs", default=0)),
                "maidens": inum(g(bw, "maidens", default=0)),
                "runs": inum(g(bw, "runs", default=0)),
                "wickets": inum(g(bw, "wickets", default=0)),
                "econ": fnum(g(bw, "economy", default=0)),
            },
            "commentary": lines,
            "inning_scores": g(mini, "matchScoreDetails", "inningScores", default=[]) or [],
        }

        # ------------------------------------------------------------------
        #  Human labels: team names, match title, venue, format
        # ------------------------------------------------------------------
        teams = extract_teams(html)
        hdr = extract_header(html, self.match_id)

        bat_id = inum(g(bt, "teamId", default=0)) or hdr.get("curr_bat_team_id", 0)
        bat = teams.get(bat_id, {})
        others = [(tid, t) for tid, t in teams.items() if tid != bat_id]
        bowl = others[0][1] if others else {}

        # <h1> is the most stable string on the page:
        #   "South Africa Champions vs West Indies Champions, 3rd Match, WCL 2026"
        h1 = hdr["h1"]
        title = h1.split(",")[0].strip() if h1 else ""
        if not bat or not bowl:
            sides = [p.strip() for p in title.split(" vs ")]
            if len(sides) == 2:
                # innings 1 -> first named side, innings 2+ -> the other one
                first = snap["innings_id"] <= 1
                bat = bat or {"name": sides[0] if first else sides[1],
                              "sname": _initials(sides[0] if first else sides[1])}
                bowl = bowl or {"name": sides[1] if first else sides[0],
                                "sname": _initials(sides[1] if first else sides[0])}

        snap["batting"] = {
            "id": bat_id,
            "name": bat.get("name", "") or "BAT",
            "code": (bat.get("sname") or _initials(bat.get("name", "")) or "BAT").upper(),
        }
        name_to_id = {v["name"]: k for k, v in teams.items() if v.get("name")}
        snap["bowling"] = {
            "id": name_to_id.get(bowl.get("name", ""), others[0][0] if others else 0),
            "name": bowl.get("name", "") or "BOWL",
            "code": (bowl.get("sname") or _initials(bowl.get("name", "")) or "BOWL").upper(),
        }
        snap["title"] = title.upper() or "%s vs %s" % (snap["batting"]["name"], snap["bowling"]["name"])
        snap["series"] = hdr.get("series", "")
        snap["match_desc"] = hdr.get("match_desc", "")
        snap["format"] = (hdr.get("format") or "").upper() or "T20"
        snap["venue"] = ", ".join(x for x in (hdr.get("ground", ""), hdr.get("city", "")) if x)
        snap["match_state"] = (hdr.get("state") or "").lower()
        ordinal = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(snap["innings_id"], "%dth" % snap["innings_id"])
        snap["innings_label"] = ordinal + " Innings"
        snap["is_live"] = snap["match_state"] in ("in progress", "toss")

        self.meta = snap
        return snap

    async def ball_by_ball(self, limit: int = 60) -> list:
        """Full ball-by-ball from the dedicated commentary page (heavier)."""
        url = "%s/live-cricket-full-commentary/%s/%s" % (CRICBUZZ, self.match_id, self.slug or "live")
        html = await self._get(url)
        if not html:
            return []
        buf = rsc_payload(html)
        d = extract_object(buf, "matchPreviewFullComm")
        out = []
        for inn in g(d, "commentary", default=[]) or []:
            for c in g(inn, "commentaryList", default=[]) or []:
                txt = (g(c, "commText", default="") or "").strip()
                if not txt:
                    continue
                out.append(
                    {
                        "text": txt,
                        "over": fnum(g(c, "ballNbr", default=0)),
                        "event": g(c, "event", default="NONE") or "NONE",
                        "timestamp": inum(g(c, "timestamp", default=0)),
                        "runs": inum(g(c, "totalRuns", default=0)),
                        "team_score": inum(g(c, "batTeamScore", default=0)),
                    }
                )
        out.sort(key=lambda x: x["timestamp"], reverse=True)
        return out[:limit]


# ---------------------------------------------------------------------------
#  Ball detector — fires exactly once per delivery
# ---------------------------------------------------------------------------
class BallDetector:
    """
    Cricbuzz gives us a *snapshot*, not a stream. We detect a new delivery by
    watching the triple (inningsId, legal balls bowled, team score, wickets).

    Runs and wickets for the delivery are derived by subtraction, which means
    we keep working even when the `event` field or commentary is missing.
    """

    def __init__(self):
        self.prev: Optional[dict] = None
        self.last_text = ""

    @staticmethod
    def _sig(s: dict):
        return (s["innings_id"], s["balls"], s["score"], s["wickets"])

    def diff(self, cur: dict) -> Optional[dict]:
        if self.prev is None:
            self.prev = cur
            return None

        p, c = self.prev, cur
        self.prev = cur

        # innings break / rain / reset
        if c["innings_id"] != p["innings_id"]:
            return {"type": "innings_break", "innings": c["innings_id"]}

        d_score = c["score"] - p["score"]
        d_wkts = c["wickets"] - p["wickets"]
        d_balls = c["balls"] - p["balls"]

        if d_balls < 0 or d_score < 0 or d_wkts < 0:   # data corrected / new innings
            return {"type": "reset"}

        if d_balls == 0 and d_score == 0 and d_wkts == 0:
            return None                                 # nothing happened yet

        # Find the freshest commentary line we haven't used yet
        text = ""
        for line in c.get("commentary", [])[:8]:
            if line["text"] and line["text"] != self.last_text:
                text = line["text"]
                break
        if text:
            self.last_text = text

        is_extra = d_balls == 0 and d_score > 0        # wide / no-ball
        legal = d_balls == 1

        over_end = False
        if legal and c["balls"] % 6 == 0 and c["balls"] > 0:
            over_end = True

        return {
            "type": "ball",
            "runs": max(0, d_score),
            "wicket": d_wkts > 0,
            "wickets_lost": d_wkts,
            "extra": is_extra,
            "legal": legal,
            "over_end": over_end,
            # cricket convention: 24 balls bowled == the 6th ball of over 3
            # ("3.6"), not "4.6". Cricbuzz overs strings are 0-indexed.
            "over_number": (c["balls"] - 1) // 6,
            "ball_in_over": ((c["balls"] - 1) % 6) + 1,
            "text_en": text,
            "this_over": c.get("this_over", []),
            "prev_over": c.get("prev_over", []),
        }


# ---------------------------------------------------------------------------
#  Polling helper
# ---------------------------------------------------------------------------
async def poll_loop(scraper: CricbuzzScraper, on_snapshot: Callable, stop: Callable):
    """Poll forever, calling on_snapshot(snap, event_or_None)."""
    det = BallDetector()
    while not stop():
        try:
            if not scraper.match_id:
                await scraper.resolve_match()
            snap = await scraper.snapshot()
            if snap:
                try:
                    ev = det.diff(snap)
                except Exception as exc:
                    LOG.exception("detector error: %s", exc)
                    ev = None
                try:
                    await on_snapshot(snap, ev)
                except Exception as exc:
                    LOG.exception("handler error: %s", exc)
            else:
                LOG.warning("empty snapshot (status=%s)", scraper.status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.exception("poll error: %s", exc)
        await asyncio.sleep(scraper.interval)
