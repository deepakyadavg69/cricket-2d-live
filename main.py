"""
main.py — orchestrator + HTTP/WebSocket server.

Run it:
    python main.py                       # auto-pick the live match
    python main.py --match 174330        # pin a specific Cricbuzz match id
    python main.py --rehearse            # no live match? run the synthetic rehearsal feed
    python main.py --port $PORT          # Render / Koyeb / Railway

Then open  http://<host>/            -> the 1920x1080 broadcast overlay
          http://<host>/api/matches  -> list of live matches (to pick a match id)
          http://<host>/api/health   -> diagnostics

PIPELINE (per delivery, ~1 second end to end)
    scraper.snapshot()
      -> BallDetector.diff()      new ball? runs? wicket? extra?
      -> classifier.classify()    SHOT_SIX / RUN_FOUR / OUT_CAUGHT ...
      -> commentary.ball_line()   English -> excited Hindi
      -> audio_engine.synth()     Hindi -> MP3 (edge-tts, cached)
      -> WS broadcast             {ball} + {tts url} -> browser animates + plays
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import time
from typing import Optional

import aiohttp
from aiohttp import WSMsgType, web

from audio_engine import AudioEngine
from classifier import classify, clean_text
from commentary import CommentaryEngine, prosody_for, milestone_line, hi_name
from classifier import WICKET_HI as WICKET_HI_TEXT
from scraper import (
    CRICBUZZ,
    CricbuzzScraper,
    balls_to_overs,
    overs_to_balls,
)

LOG = logging.getLogger("main")

ROOT = os.path.dirname(os.path.abspath(__file__))
OVERLAY_DIR = os.path.join(ROOT, "overlay")
CACHE_DIR = os.path.join(ROOT, "cache", "tts")
ASSETS_DIR = os.path.join(ROOT, "assets")

MAX_OVERS = {"T20": 20, "T20I": 20, "IT20": 20, "ODI": 50, "TEST": 90, "T10": 10, "100B": 20}
TEAM_COLORS = [
    "#0a4bd6", "#e8112d", "#0a8f4b", "#ff9d3d", "#7b2fd6",
    "#00b3c4", "#d6083b", "#1a6f3c", "#f2c200", "#2b3a8f",
]


# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------
# config.yaml is baked into the deploy bundle, so on Render you change settings
# from the dashboard instead of editing files. Any of these env vars wins over
# the YAML value.  Dashboard -> your service -> Environment -> Add Environment Variable.
ENV_OVERRIDES = {
    "MATCH_ID":      ("scraper", "match_id", str),
    "SERIES_FILTER": ("scraper", "series_filter", str),
    "POLL_SECONDS":  ("scraper", "poll_seconds", float),
    "TTS_VOICE":     ("audio", "voice", str),
    "TTS_RATE":      ("audio", "rate", str),
    "BRAND_CHANNEL": ("branding", "channel", str),
    "BRAND_HANDLE":  ("branding", "handle", str),
    "BRAND_LOGO":    ("branding", "logo", str),
    "BRAND_ACCENT":  ("branding", "accent", str),
    "BRAND_TAGLINE": ("branding", "tagline", str),
    "FULL_TEAM_NAMES": ("privacy", "use_full_team_names", lambda v: str(v).lower() in ("1", "true", "yes", "on")),
    "GEMINI_API_KEY": ("commentary", "api_key_env", str),
    "PLAYOUT_DELAY": ("playout", "delay_seconds", float),
    "PLAYOUT_GAP":   ("playout", "min_gap_seconds", float),
}


def load_config(path: str) -> dict:
    cfg = {
        "scraper": {"match_id": "auto", "series_filter": "", "poll_seconds": 5},
        "audio": {"enabled": True, "voice": "hi-IN-MadhurNeural", "rate": "+12%", "pitch": "+6Hz"},
        "commentary": {"enabled": False, "mode": "template", "provider": "gemini"},
        "overlay": {"viewer_base": 12000},
        "server": {"port": 8080, "host": "0.0.0.0"},
        "playout": {"delay_seconds": 10.0, "min_gap_seconds": 2.2},
    }
    try:
        import yaml  # type: ignore

        with open(path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        LOG.info("Loaded %s", path)
    except FileNotFoundError:
        LOG.info("No config.yaml -> using defaults")
    except Exception as exc:
        LOG.warning("config.yaml unreadable (%s) -> defaults", exc)

    # env vars override YAML (this is how you configure a deployed instance)
    for var, (section, key, cast) in ENV_OVERRIDES.items():
        raw = os.getenv(var)
        if raw is None or raw == "":
            continue
        try:
            cfg.setdefault(section, {})[key] = cast(raw)
            LOG.info("env override: %s -> %s.%s", var, section, key)
        except Exception as exc:
            LOG.warning("bad env value %s=%r (%s)", var, raw, exc)

    # convenience: enabling LLM commentary via a key alone
    if os.getenv("GEMINI_API_KEY"):
        cfg["commentary"]["enabled"] = True
        cfg["commentary"]["mode"] = "llm"
        cfg["commentary"]["provider"] = "gemini"

    return cfg


def team_color(code: str) -> str:
    if not code:
        return TEAM_COLORS[0]
    return TEAM_COLORS[sum(ord(c) for c in code) % len(TEAM_COLORS)]


def jersey(pid: int, name: str) -> int:
    if pid:
        n = pid % 99
        return n if n > 0 else 99
    return (sum(ord(c) for c in name) % 99) or 99


class _RestartLoop(Exception):
    pass


# ---------------------------------------------------------------------------
#  Smart extras
# ---------------------------------------------------------------------------
MILESTONE_HI = {50: "FIFTY", 100: "HUNDRED", 150: "150", 200: "DOUBLE HUNDRED",
                250: "250", 300: "TRIPLE HUNDRED"}


def parse_dismissal(text: str) -> str:
    """
    Cricbuzz `lastWicket` is a single string like:
        'Sunil Kumar  c Shikhar Mohan b Mukesh Kumar 0(1)  - 111/10 in 28.4 ov.'
        'Lone Nasir Muzaffar b Mukesh Kumar 1(3)'
    We recover the dismissal type from it, which is far more reliable than
    guessing from commentary text (which is often missing entirely).
    """
    if not text:
        return ""
    low = " " + text.lower() + " "
    if "run out" in low or "run-out" in low:
        return "OUT_RUNOUT"
    if "hit wicket" in low or "hit-wicket" in low:
        return "OUT_HITWICKET"
    if " st " in low:
        return "OUT_STUMPED"
    if "lbw" in low:
        return "OUT_LBW"
    if " c " in low or " c&b " in low or " c and b " in low:
        return "OUT_CAUGHT"
    if " b " in low:
        return "OUT_BOWLED"
    return ""


def _player_key(p: dict) -> str:
    """
    Identify a player by NAME first, id second. Cricbuzz re-issues player ids
    between polls often enough that keying on id alone re-announces the same
    fifty several times an innings; names are stable.
    """
    n = (p.get("name") or "").strip().upper()
    if n:
        return n
    return "#" + str(p.get("id") or "")


def detect_milestones(snap: dict, st) -> list:
    """
    Fire once when a batter crosses 50/100/... or a bowler takes 5/7/10.

    Dedup is by (kind, player, value) in a *set*, not by a monotonic counter.
    That matters: Cricbuzz sometimes changes a player's id between polls and
    its CDN can hand back an older page, and either one would make a counter
    reset and re-announce "FIFTY!" several times in the same innings.
    """
    out = []
    fired = st._ms_fired

    bat = snap.get("striker", {}) or {}
    bkey = _player_key(bat)
    runs = int(bat.get("runs", 0) or 0)
    if bkey:
        for m in sorted(MILESTONE_HI, reverse=True):
            if runs >= m and ("bat", bkey, m) not in fired:
                fired.add(("bat", bkey, m))
                out.append({"kind": "bat", "value": m, "label": MILESTONE_HI[m]})
                break

    bw = snap.get("bowler", {}) or {}
    wkey = _player_key(bw)
    wk = int(bw.get("wickets", 0) or 0)
    if wkey:
        for m in (5, 7, 10):
            if wk >= m and ("bowl", wkey, m) not in fired:
                fired.add(("bowl", wkey, m))
                out.append({"kind": "bowl", "value": m, "label": "%d WICKET HAUL" % m})
                break

    if len(fired) > 400:          # long tournament? drop the oldest half
        for k in list(fired)[:200]:
            fired.discard(k)
    return out


# ---------------------------------------------------------------------------
#  aiohttp >= 3.14 dropped `ensure_ascii=` from json_response, so we serialise
#  ourselves. This keeps Devanagari readable instead of \u092f escapes.
# ---------------------------------------------------------------------------
def json_resp(obj, status=200):
    return web.Response(
        text=json.dumps(obj, ensure_ascii=False, default=str),
        content_type="application/json",
        status=status,
    )


# ---------------------------------------------------------------------------
#  Overlay state builder
# ---------------------------------------------------------------------------
def overlay_state(snap: dict, st: "ServerState", hi: str = "", en: str = "") -> dict:
    fmt = (snap.get("format") or "T20").upper()
    max_ov = MAX_OVERS.get(fmt, 20)
    bat_code = snap.get("batting", {}).get("code") or "BAT"
    bowl_code = snap.get("bowling", {}).get("code") or "BOWL"

    bowler_ov = snap.get("bowler", {}).get("overs", 0.0) or 0.0

    # safe mode: sirf short codes dikhayein, poora team naam nahi
    title = snap.get("title") or "LIVE CRICKET"
    if not st.privacy.get("use_full_team_names", False):
        bat_code = snap.get("batting", {}).get("code") or ""
        bowl_code = snap.get("bowling", {}).get("code") or ""
        if bat_code and bowl_code:
            title = "%s VS %s" % (bat_code, bowl_code)

    def _hi(p):
        return dict(p, hi=hi_name(p.get("name", ""))) if isinstance(p, dict) else p

    return {
        "match": {
            "title": title,
            "format": fmt,
            "venue": snap.get("venue") or snap.get("series") or "",
            "innings": snap.get("innings_label") or "1st Innings",
            "chase": bool(snap.get("target")),
            "series": snap.get("series") or "",
        },
        "batting": {
            "code": bat_code,
            "name": snap.get("batting", {}).get("name") or "",
            "color": team_color(bat_code),
            "score": snap.get("score", 0),
            "wkts": snap.get("wickets", 0),
            "balls": snap.get("balls", 0),
            "maxOvers": max_ov,
        },
        "bowling": {
            "code": bowl_code,
            "name": snap.get("bowling", {}).get("name") or "",
            "color": team_color(bowl_code),
        },
        "target": snap.get("target", 0),
        "crr": snap.get("crr", 0.0),
        "rrr": snap.get("rrr", 0.0),
        "need": snap.get("need", 0),
        "ballsLeft": snap.get("balls_left", 0),
        # NOTE: jersey number jaan-bujh kar NAHI bhejte. Cricbuzz ye data deta
        # hi nahi, aur nakli number banana galat jaankari dikhana hai —
        # "sirf naam, koi number nahi" (user ki pasand).
        "striker": _hi(snap.get("striker", {})),
        "nonStriker": _hi(snap.get("non_striker", {})),
        "bowler": _hi(dict(snap.get("bowler", {}), balls=overs_to_balls(bowler_ov))),
        "thisOver": snap.get("this_over", []),
        "lastOver": snap.get("prev_over", []),
        "partnership": snap.get("partnership", 0),
        "commentary_hi": hi,
        "commentary_en": en,
        "isLive": snap.get("is_live", False),
        "matchState": snap.get("match_state", ""),
        "serverTime": time.time(),
        "branding": st.branding,
        "privacy": st.privacy,
    }



# ---------------------------------------------------------------------------
#  Playout buffer — "10 second late, par bilkul ek saath"
# ---------------------------------------------------------------------------
class PlayoutBuffer:
    """
    Asli TV channels koi bhi cheez hote hi hawa mein nahi bhejte — sab kuch ek
    playout server se guzarta hai jismein ek *fixed* delay hoti hai. Isi wajah
    se picture, score-bug aur commentary aapas mein chipke dikhte hain, ek dusre
    se aage-peeche nahi hote.

    Bina iske kya hota tha: Cricbuzz ek baar mein 3 ball de de (missed poll),
    to commentary 3 line ek saath bol padti thi, animation tez ho jati thi aur
    scoreboard achanak kood jata tha — stream "alag alag" lagne lagti thi.

    Ab har event queue mein jata hai aur theek `delay` second baad, aur kam se
    kam `min_gap` second ke faasle se nikalta hai. Result: hamesha ek hi raftaar.

    delay    = kitni der baad hawa mein jaye (default 10 second)
    min_gap  = do bade events ke beech kam se kam kitna waqt (burst rokta hai)
    """

    def __init__(self, delay: float = 10.0, min_gap: float = 2.2):
        self.delay = delay
        self.min_gap = min_gap          # sirf "gap wale" events ke liye
        self.q: "asyncio.Queue" = asyncio.Queue()
        self.last_emit = 0.0
        self.pending = 0
        # group -> kab emit hua. Isse pata chalta hai ki "ye ball ka group
        # nikal chuka hai", chahe beech mein over-card aa gaya ho.
        self.seen_groups: dict = {}

    def push(self, at: float, fn, tag: str = "", gap: bool = False, group=None):
        self.q.put_nowait((at, fn, tag, gap, group))
        self.pending += 1

    async def _run(self):
        while True:
            at, fn, tag, gap, group = await self.q.get()

            # Ye group nikal chuka hai? (ball animation + SFX + awaaz ek hi
            # group hain). TTS synthesis mein 1-4 second lag sakta hai, tab tak
            # over-card bhi nikal chuka hota tha — purana code usse "naya group"
            # samajh kar 2.2 second ka faasla daal deta tha, aur awaaz ball ke
            # 4 second baad sunayi deti thi. Ab group yaad rakhte hain.
            now = time.time()
            already = group is not None and group in self.seen_groups

            if already:
                wait = at + self.delay - now
            else:
                target = at + self.delay
                floor = (self.last_emit + self.min_gap) if gap else 0.0
                wait = max(target, floor) - now

            if wait > 0:
                await asyncio.sleep(wait)
            try:
                await fn()
            except Exception as exc:
                LOG.warning("playout emit fail (%s): %s", tag, exc)

            if gap and not already:
                self.last_emit = time.time()
            if group is not None:
                self.seen_groups[group] = time.time()
                # purane group yaad mat rakho (yaad had se na badhe)
                if len(self.seen_groups) > 200:
                    cut = time.time() - 300
                    for k in [k for k, t in self.seen_groups.items() if t < cut]:
                        self.seen_groups.pop(k, None)
            self.pending -= 1

    def start(self):
        asyncio.create_task(self._run())


# ---------------------------------------------------------------------------
#  Shared server state
# ---------------------------------------------------------------------------
class ServerState:
    def __init__(self):
        self.snap: Optional[dict] = None
        self.overlay: dict = {}
        self.last_hi = ""
        self.last_en = ""
        self.over_events: list = []     # timeline badges of the current over
        self.last_over_number = -1
        # defaults; App.__init__ overwrites these from config.yaml
        self.branding = {
            "channel": "CRICKET LIVE", "handle": "@yourchannel", "logo": "C",
            "accent": "#1de9b6", "tagline": "AUTO 2D ENGINE",
        }
        self.privacy = {
            "use_full_team_names": False,
            "block_logos": True,
        }
        self.last_success = time.time()
        self.last_ball_at = 0.0
        self._last_bat_id = None
        self._bat_ms = 0
        self._last_bowl_id = None
        self._bowl_ms = 0
        self._ms_fired: set = set()   # (kind, player_key, value) — dekh detect_milestones
        self.status = {
            "mode": "starting",        # live | rehearse | idle
            "scraper": "starting",
            "match_id": None,
            "title": "",
            "balls_seen": 0,
            "tts": False,
            "last_ball_at": 0,
            "errors": 0,
        }


ADMIN_HTML = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Cricket 2D — Control</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#071320;color:#e8f1f8;font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;padding:18px}
h1{font-size:22px;margin-bottom:4px}
.sub{color:#8fb2cc;font-size:14px;margin-bottom:16px}
.card{background:#0d2038;border:1px solid #1c3350;border-radius:12px;padding:14px;margin-bottom:14px;border-color:#1c3350}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,button{font-size:16px;padding:11px 14px;border-radius:9px;border:1px solid #2a4460}
input{background:#08131f;color:#e8f1f8;flex:1;min-width:130px;border-color:#2a4460}
button{background:#1de9b6;color:#04241c;font-weight:800;border:0;cursor:pointer}
button.ghost{background:#16324e;color:#cfe4f5}
.m{display:block;width:100%;text-align:left;background:#0a1728;border:1px solid #24405f;color:#e8f1f8;
   border-radius:10px;padding:12px 14px;margin-bottom:9px;cursor:pointer}
.m:hover{background:#12283f}
.m b{font-size:17px}
.m small{display:block;color:#8fb2cc;font-size:13px;margin-top:2px}
.live{color:#1de9b6;font-weight:800}
.ok{color:#1de9b6}.bad{color:#ff8a9c}
#msg{margin-top:10px;font-size:14px}
.h{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.pill{font-size:12px;font-weight:800;letter-spacing:1px;padding:5px 10px;border-radius:20px;
  background:#16324e;color:#8fb2cc}
.pill.on{background:#1de9b6;color:#04241c}
</style></head><body>
<h1>🏏 Cricket 2D — Control</h1>
<div class="sub">Match choose karein · <span id=st>loading…</span></div>

<div class=card>
  <div class=h><b>Current status</b><span class="pill" id=mode>—</span></div>
  <div id=cur style="font-size:14px;color:#a9c6dc">—</div>
</div>

<div class=card>
  <div class=h><b>Match ID seedha daalein</b></div>
  <div class=row>
    <input id=mid placeholder="e.g. 174330" inputmode=numeric>
    <button onclick="setMatch(document.getElementById('mid').value)">Set</button>
  </div>
  <div id=msg></div>
</div>

<div class=card>
  <div class=h><b>Live matches</b><button class=ghost onclick="load()">Refresh</button></div>
  <div id=list style="margin-top:10px">loading…</div>
</div>

<script>
async function status(){
  try{
    const r = await fetch('/api/health'); const d = await r.json();
    const p = document.getElementById('mode');
    p.textContent = (d.status.mode||'').toUpperCase();
    p.className = 'pill' + (d.status.mode==='live'?' on':'');
    document.getElementById('st').innerHTML =
      'backend <span class=ok>'+(d.scraper||'?')+'</span> · TTS '+
      (d.assets.tts?'<span class=ok>on</span>':'<span class=bad>off</span>')+
      ' · clients '+d.clients;
    document.getElementById('cur').innerHTML =
      '<b>'+ (d.match.title||'—') +'</b><br>match id '+d.match.id+
      ' · balls seen '+d.status.balls_seen+
      '<br><a href="'+d.match.url+'" target=_blank style="color:#1de9b6">cricbuzz par dekhein</a>';
  }catch(e){ document.getElementById('st').textContent='backend offline'; }
}
async function load(){
  const L = document.getElementById('list'); L.textContent='loading…';
  try{
    const r = await fetch('/api/matches'); const d = await r.json();
    if(!d.matches||!d.matches.length){ L.textContent='koi match nahi mila'; return; }
    L.innerHTML='';
    d.matches.forEach(m=>{
      const b=document.createElement('button'); b.className='m';
      const live=/in progress|toss/i.test(m.state||'');
      b.innerHTML='<b>'+(m.team1||'?')+' vs '+(m.team2||'?')+'</b>'+
        '<small><span class="'+(live?'live':'')+'">'+(m.state||'')+'</span> · '+
        (m.format||'')+' · '+(m.series||'')+'</small>'+
        '<small>ID '+m.match_id+'</small>';
      b.onclick=()=>setMatch(m.match_id);
      L.appendChild(b);
    });
  }catch(e){ L.textContent='error: '+e; }
}
async function setMatch(id){
  const msg=document.getElementById('msg');
  if(!/^[0-9]+$/.test(String(id||'').trim())){ msg.innerHTML='<span class=bad>galt ID</span>'; return; }
  msg.textContent='switching…';
  try{
    const r=await fetch('/api/match?match_id='+encodeURIComponent(id));
    const d=await r.json();
    msg.innerHTML = d.ok ? '<span class=ok>✓ match '+id+' set</span>' : '<span class=bad>'+(d.error||'fail')+'</span>';
    setTimeout(()=>{status();load();},1200);
  }catch(e){ msg.innerHTML='<span class=bad>'+e+'</span>'; }
}
status(); load();
setInterval(status, 15000);
</script></body></html>"""


# ---------------------------------------------------------------------------
#  App
# ---------------------------------------------------------------------------
class App:
    def __init__(self, cfg: dict, args):
        self.cfg = cfg
        self.args = args
        self.state = ServerState()
        self.clients = set()

        self.scraper = CricbuzzScraper(
            match_id=args.match or str(cfg["scraper"].get("match_id", "auto")),
            interval=float(args.poll or cfg["scraper"].get("poll_seconds", 5)),
            series_filter=cfg["scraper"].get("series_filter", "") or "",
        )
        self.branding = {
            "channel": (cfg.get("branding", {}) or {}).get("channel", "CRICKET LIVE"),
            "handle":  (cfg.get("branding", {}) or {}).get("handle",  "@yourchannel"),
            "logo":    (cfg.get("branding", {}) or {}).get("logo",    "C"),
            "accent":  (cfg.get("branding", {}) or {}).get("accent",  "#1de9b6"),
            "tagline": (cfg.get("branding", {}) or {}).get("tagline", "AUTO 2D ENGINE"),
        }
        self.privacy = {
            "use_full_team_names": bool((cfg.get("privacy", {}) or {}).get("use_full_team_names", False)),
            "block_logos":         bool((cfg.get("privacy", {}) or {}).get("block_logos", True)),
        }
        self.state.branding = self.branding
        self.state.privacy  = self.privacy
        self.audio = AudioEngine(cfg.get("audio", {}))
        self.commentary = CommentaryEngine(cfg.get("commentary", {}))
        self.stop_evt = asyncio.Event()
        self.want_rehearsal = False
        self.want_switch = False
        self.want_live = False
        # playout: sab kuch fixed delay se hawa mein jata hai (smooth stream)
        self.playout = PlayoutBuffer(
            delay=float(cfg.get("playout", {}).get("delay_seconds", 10.0)),
            min_gap=float(cfg.get("playout", {}).get("min_gap_seconds", 2.2)))
        # NOTE: .start() run() mein hota hai — yahan abhi event loop chalu nahi
        # hota, isliye asyncio.create_task() fail ho jata.

    # -- websocket helpers --------------------------------------------------
    def emit(self, msg: dict, gap: bool = False, at: float = None, group=None):
        """
        Overlay ko bhejo — par seedha nahi, playout buffer se hokar.
        gap=True wale events ek dusre se kam se kam `min_gap` second door rehte
        hain (taaki kabhi ek saath na ghus jayein). Ek hi `group` ke saare
        hissa (ball + uski awaaz + uska SFX) ek saath chalte hain.
        """
        self.playout.push(at if at is not None else time.time(),
                          (lambda: self.broadcast(msg)), msg.get("type", ""), gap, group)

    async def broadcast(self, msg: dict):
        if not self.clients:
            return
        data = json.dumps(msg, ensure_ascii=False)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_str(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    # -- core: handle one detected delivery ---------------------------------
    async def handle_ball(self, snap: dict, ev: dict):
        st = self.state
        st.status["balls_seen"] += 1
        st.status["last_ball_at"] = time.time()
        st.last_ball_at = time.time()

        text_en = clean_text(ev.get("text_en", ""))
        cls = classify(text_en, ev.get("runs", 0), ev.get("wicket", False), ev.get("extra", False))

        # commentary text is frequently missing -> recover the dismissal type
        # from Cricbuzz's `lastWicket` string instead of guessing
        if cls["is_wicket"]:
            dtype = parse_dismissal(snap.get("last_wicket", ""))
            if dtype:
                cls["wicket_type"] = dtype
                cls["wicket_hi"] = WICKET_HI_TEXT.get(dtype, "आउट")

        ctx = {
            "runs": ev.get("runs", 0),
            "score": snap.get("score", 0),
            "wickets": snap.get("wickets", 0),
            "target": snap.get("target", 0),
            "need": snap.get("need", 0),
            "balls_left": snap.get("balls_left", 0),
            "striker_name": snap.get("striker", {}).get("name", ""),
            "non_striker_name": snap.get("non_striker", {}).get("name", ""),
            "bowler_name": snap.get("bowler", {}).get("name", ""),
            "striker_runs": snap.get("striker", {}).get("runs", 0),
            "striker_balls": snap.get("striker", {}).get("balls", 0),
        }

        hi = await self.commentary.ball_line(cls, ctx, text_en)
        st.last_hi, st.last_en = hi, text_en

        # badge for the over timeline
        if ev.get("wicket"):
            badge = "W"
        elif ev.get("extra") and ev.get("runs", 0):
            badge = "wd"
        else:
            badge = str(ev.get("runs", 0))

        over_no = ev.get("over_number", 0)
        if over_no != st.last_over_number:
            if st.over_events:
                st.status.setdefault("prev_over_events", st.over_events)
            st.over_events = []
            st.last_over_number = over_no
        st.over_events.append(badge)

        # ---- to the browser (ATOMIC BUNDLE, playout buffer se hokar) --------
        #
        # Ek ball ke teeno hissa (animation + awaaz + SFX) HAMESHA ek saath aur
        # theek order mein jane chahiye. Pehle hum awaaz ko alag task se bhejte
        # the — par edge-tts ko 1-4 second lagte hain, aur tab tak queue mein
        # doosre ball aa jate the, to awaaz apne ball ke 2-4 second baad chalti
        # thi. Ab awaaz pehle taiyaar karte hain, phir teeno ko EK SAATH queue
        # mein dalte hain — par `at` ball ka asli waqt rakhte hain, isse poora
        # bundle theek ball_at + delay par chalta hai (synth ka time judta nahi).
        ball_at = time.time()
        grp = "ball-%d" % st.status["balls_seen"]
        pro = prosody_for(cls)
        sfx = ("six" if cls["is_six"] else
               "four" if cls["is_four"] else
               "wicket" if cls["is_wicket"] else None)

        ball_msg = {
            "type": "ball",
            "payload": {
                "runs": ev.get("runs", 0),
                "extra": bool(ev.get("extra")),
                "wicket": bool(ev.get("wicket")),
                "shot": cls["shot"],
                "event": "WICKET" if cls["is_wicket"] else cls["event"],
                "wicket_type": cls.get("wicket_type"),
                "milestone": cls.get("milestone"),
                "text_hi": hi,
                "text_en": text_en,
                "over": over_no,
                "ball_in_over": ev.get("ball_in_over", 0),
                "over_end": ev.get("over_end", False),
            },
        }

        async def emit_ball_bundle():
            url = None
            if hi and self.audio.enabled:
                try:
                    url = await asyncio.wait_for(
                        self.audio.synth(hi, over=False, rate=pro["rate"], pitch=pro["pitch"]),
                        timeout=8.0)
                except Exception as exc:
                    LOG.debug("ball tts synth: %s", exc)
            # ORDER MAT BADALNA: ball -> sfx -> awaaz
            self.emit(ball_msg, gap=True, at=ball_at, group=grp)
            if sfx:
                self.emit({"type": "sfx", "payload": {"name": sfx}},
                          gap=True, at=ball_at, group=grp)
            if url:
                self.state.status["tts"] = True
                self.emit({"type": "tts",
                           "payload": {"url": url, "text": hi, "kind": "ball"}},
                          gap=True, at=ball_at, group=grp)

        asyncio.create_task(emit_ball_bundle())

        LOG.info("[ball %s.%s] %s %s | %s | %s",
                 over_no, ev.get("ball_in_over"), ev.get("runs"), "W" if ev.get("wicket") else "",
                 cls["shot"], (hi or "")[:60])

        # ---- over end --------------------------------------------------------
        if ev.get("over_end"):
            await self.handle_over_end(snap, over_no, st.over_events)

    async def speak(self, text: str, kind: str = "ball", rate: str = None,
                    pitch: str = None, at: float = None, group=None):
        # synth abhi (taaki waqt par taiyaar ho), broadcast playout se
        url = await self.audio.synth(text, over=(kind == "over"), rate=rate, pitch=pitch)
        if not url:
            return
        self.state.status["tts"] = True
        self.emit({"type": "tts", "payload": {"url": url, "text": text, "kind": kind}},
                  gap=True, at=at, group=group)

    async def handle_over_end(self, snap: dict, over_no: int, timeline: list):
        ctx = {
            "score": snap.get("score", 0),
            "wickets": snap.get("wickets", 0),
            "target": snap.get("target", 0),
            "need": snap.get("need", 0),
            "balls_left": snap.get("balls_left", 0),
            "striker_name": snap.get("striker", {}).get("name", ""),
            "non_striker_name": snap.get("non_striker", {}).get("name", ""),
            "bowler_name": snap.get("bowler", {}).get("name", ""),
        }
        runs = sum(int(t) for t in timeline if str(t).isdigit())
        line = await self.commentary.over_line(over_no, runs, timeline, ctx)
        self.emit({
            "type": "over_end",
            "payload": {"over": over_no, "runs": runs, "timeline": timeline, "text_hi": line},
        }, gap=True, group="over-%d" % over_no)
        if self.audio.enabled:
            asyncio.create_task(self.speak(line, kind="over", group="over-%d" % over_no))
        LOG.info("[over %s] %s runs | %s", over_no, runs, timeline)

    # -- scraper callback ---------------------------------------------------
    async def on_snapshot(self, snap: dict, ev):
        st = self.state
        st.snap = snap
        st.last_success = time.time()
        st.status["scraper"] = "ok"
        st.status["match_id"] = snap.get("match_id")
        st.status["title"] = snap.get("title", "")

        if ev and ev.get("type") == "ball":
            await self.handle_ball(snap, ev)
        elif ev and ev.get("type") == "innings_break":
            st.over_events = []
            await self.broadcast({"type": "status", "payload": st.status})
        elif ev and ev.get("type") == "stale":
            # CDN served an older page — ignore it, keep the over timeline intact
            pass
        elif ev and ev.get("type") == "catchup":
            # score resync (missed balls / site correction) — update silently,
            # do NOT animate or speak a fake delivery
            LOG.info("score resync: +%s runs, +%s wkts (no ball event)",
                     ev.get("runs", 0), ev.get("wickets", 0))
            st.over_events = []
            await self.broadcast({"type": "status", "payload": st.status})

        # milestones (fifty / century / five-for) — fire once each
        for ms in detect_milestones(snap, st):
            self.emit({"type": "milestone", "payload": ms}, gap=True)
            LOG.info("[milestone] %s %s", ms["kind"], ms["label"])
            if self.audio.enabled:
                line = milestone_line(
                    "FIFTY" if ms["value"] == 50 else "HUNDRED" if ms["value"] == 100 else "FIVE_FOR",
                    {"striker_name": snap.get("striker", {}).get("name", ""),
                     "bowler_name": snap.get("bowler", {}).get("name", "")},
                )
                if line:
                    asyncio.create_task(self.speak(
                        line, kind="ball",
                        rate=prosody_for({"milestone": True})["rate"],
                        pitch=prosody_for({"milestone": True})["pitch"]))

        st.overlay = overlay_state(snap, st, st.last_hi, st.last_en)
        await self.broadcast({"type": "state", "payload": st.overlay})

    # -- rehearsal feed (no live match right now) ---------------------------
    async def rehearsal_loop(self):
        """
        Nothing live? Generate a plausible match so you can still rehearse the
        full chain (animation + Hindi TTS + ducking + over cards) end to end.
        """
        from scraper import BallDetector

        st = self.state
        st.status["mode"] = "rehearse"
        LOG.warning("No live match available -> REHEARSAL MODE (synthetic feed)")

        score, wkts, balls, target = 96, 3, 72, 187
        names = ["V KOHLI", "KL RAHUL", "S IYER", "R SHARMA", "H PANDYA"]
        bowlers = ["M STARC", "J HAZLEWOOD", "A ZAMPA"]
        si, bi = 0, 0
        det = BallDetector()
        seq = [0, 1, 4, 0, 6, 2, 1, 0, "W", 4, 1, 6, 0, 2, 1, 3, 0, 0, 6, 1]

        last_probe = time.time()
        while not self.stop_evt.is_set():
            # Rehearsal is a safety net, not a destination. Every 5 minutes look
            # for a real live match again — otherwise a server started during a
            # quiet period would rehearse forever and never show the real game.
            if time.time() - last_probe > 300:
                last_probe = time.time()
                try:
                    self.scraper.match_id = None
                    if await self.scraper.resolve_match():
                        probe = await self.scraper.snapshot()
                        if probe and probe.get("is_live"):
                            LOG.info("Live match mil gaya -> rehearsal se live par ja rahe hain")
                            self.want_live = True
                            return
                except Exception as exc:
                    LOG.debug("live probe failed: %s", exc)

            v = seq[(st.status["balls_seen"]) % len(seq)]
            wicket = v == "W"
            runs = 0 if wicket else int(v)
            balls += 1
            score += runs
            if wicket:
                wkts += 1
                si += 1
            if runs % 2 == 1:
                pass  # strike rotation is cosmetic here
            if wicket and wkts >= 9:
                wkts, score = 3, 96   # loop the rehearsal

            sr_balls = max(1, (balls // 4) + si)
            snap = {
                "match_id": "rehearsal", "title": "REHEARSAL · DEMO MATCH",
                "series": "Rehearsal Mode", "match_desc": "Demo", "format": "T20",
                "venue": "Your Tablet", "match_state": "in progress", "is_live": True,
                "innings_id": 2, "innings_label": "2nd Innings",
                "batting": {"id": 1, "name": "INDIA", "code": "IND"},
                "bowling": {"id": 2, "name": "AUSTRALIA", "code": "AUS"},
                "score": score, "wickets": wkts, "overs": balls_to_overs(balls),
                "balls": balls, "target": target,
                "crr": round(score / max(0.1, balls / 6), 2),
                "rrr": round(max(0, target - score) / max(0.1, (120 - balls) / 6), 2),
                "need": max(0, target - score), "balls_left": max(0, 120 - balls),
                "partnership": 34,
                "striker": {"id": 100 + si, "name": names[si % len(names)], "runs": (score // 3) + si,
                            "balls": sr_balls, "fours": 4, "sixes": 2, "sr": 140.0},
                "non_striker": {"id": 200 + si, "name": names[(si + 1) % len(names)], "runs": 28,
                                "balls": 21, "fours": 2, "sixes": 1, "sr": 133.0},
                "bowler": {"id": 300 + bi, "name": bowlers[bi % len(bowlers)],
                           "overs": round(balls / 6, 1), "maidens": 0, "runs": score // 2,
                           "wickets": wkts, "econ": 8.4},
                "this_over": [], "prev_over": ["1", "4", "0", "6", "W", "2"],
                "recent_ovs": "", "commentary": [], "inning_scores": [],
            }
            lines = {
                6: "Lofted down the ground, that's gone all the way for SIX!",
                4: "Driven sweetly through the covers, FOUR more!",
                1: "Dabbed into the gap, quick single taken.",
                2: "Worked into the leg side, they come back for two.",
                3: "Excellent running, three runs completed!",
                0: "Back of a length, defended solidly, no run.",
            }
            text_en = "OUT! Caught at deep mid-wicket!" if wicket else lines.get(runs, "")
            over_end = balls % 6 == 0
            if over_end:
                bi += 1
            ev = {
                "type": "ball", "runs": runs, "wicket": wicket, "extra": False, "legal": True,
                "over_end": over_end,
                "over_number": (balls - 1) // 6,
                "ball_in_over": ((balls - 1) % 6) + 1, "text_en": text_en,
                "this_over": [], "prev_over": [],
            }
            await self.on_snapshot(snap, ev)
            await asyncio.sleep(max(3.0, float(self.cfg["scraper"].get("poll_seconds", 5)) - 1.0))

    # -- watchdog: stream kabhi mara nahi hona chahiye ----------------------
    async def watchdog(self):
        """
        Two jobs, both about never letting the broadcast die mid-match:

        1. STALE SCRAPER  - if Cricbuzz stops answering (blocked IP, redesign,
           network blip) for 3 minutes, fall back to the rehearsal feed so the
           stream keeps animating instead of freezing on a dead scoreboard.

        2. MATCH FINISHED - if the current match is Complete/Result and no new
           ball has arrived for 5 minutes, auto-switch to the next live match.
        """
        st = self.state
        last_switch_check = 0.0
        while not self.stop_evt.is_set():
            await asyncio.sleep(20)
            now = time.time()

            if now - st.last_success > 180:
                LOG.warning("scraper stale for %.0fs -> rehearsal", now - st.last_success)
                self.want_rehearsal = True
                return

            if now - last_switch_check > 120:
                last_switch_check = now
                snap = st.snap or {}
                finished = snap.get("match_state") in ("complete", "result")
                idle = (now - st.last_ball_at) if st.last_ball_at else 999
                if finished and idle > 300:
                    LOG.info("match finished (idle %.0fs) -> auto-switch", idle)
                    self.want_switch = True
                    return

    # -- main poll loop -----------------------------------------------------
    async def live_loop(self):
        st = self.state
        await self.scraper.start()

        async def rehearse():
            """Rehearsal chalate hain; True = live match mil gaya, dobara try karo."""
            await self.rehearsal_loop()
            if self.want_live:
                self.want_live = False
                return True
            return False

        for _attempt in range(12):
            self.want_rehearsal = False
            self.want_switch = False
            try:
                ok = await self.scraper.resolve_match()
                if not ok:
                    st.status["scraper"] = "no_match"
                    await self.broadcast({"type": "status", "payload": st.status})
                    if await rehearse():
                        continue
                    return

                snap = await self.scraper.snapshot()
                if not snap:
                    if await rehearse():
                        continue
                    return

                # --rehearse = force the synthetic feed (practice bina live match ke)
                if self.args.rehearse or not snap.get("is_live"):
                    if not snap.get("is_live"):
                        LOG.info("Auto-picked match is not live -> rehearsal feed")
                    if await rehearse():
                        continue
                    return

                st.status["mode"] = "live"
                LOG.info("Streaming match %s — %s", self.scraper.match_id, snap.get("title"))
                await self.broadcast({"type": "status", "payload": st.status})

                from scraper import poll_loop

                poll = asyncio.create_task(
                    poll_loop(self.scraper, self.on_snapshot, self.stop_evt.is_set))
                watch = asyncio.create_task(self.watchdog())
                _done, pending = await asyncio.wait(
                    {poll, watch}, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                if self.want_rehearsal:
                    LOG.warning("Falling back to rehearsal feed")
                    if await rehearse():
                        continue
                    return
                if self.want_switch:
                    LOG.info("Match khatam — agla live match dhoondh rahe hain…")
                    self.scraper.match_id = None
                    self.scraper.slug = "live"
                    st.last_over_number = -1
                    st.over_events = []
                    st.last_success = time.time()
                    await asyncio.sleep(5)
                    continue
                return
            except _RestartLoop:
                continue
        LOG.error("too many match switches, giving up")
        if not ok:
            st.status["scraper"] = "no_match"
            await self.broadcast({"type": "status", "payload": st.status})
            await self.rehearsal_loop()
            return

        snap = await self.scraper.snapshot()
        if not snap:
            await self.rehearsal_loop()
            return

        if not snap.get("is_live") and self.args.rehearse:
            await self.rehearsal_loop()
            return

        st.status["mode"] = "live"
        LOG.info("Streaming match %s — %s", self.scraper.match_id, snap.get("title"))
        await self.broadcast({"type": "status", "payload": st.status})

        from scraper import poll_loop

        poll = asyncio.create_task(poll_loop(self.scraper, self.on_snapshot, self.stop_evt.is_set))
        watch = asyncio.create_task(self.watchdog())
        _done, pending = await asyncio.wait({poll, watch}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

                return

    # -- HTTP handlers ------------------------------------------------------
    async def h_index(self, request):
        return web.FileResponse(os.path.join(OVERLAY_DIR, "index.html"))

    async def h_admin(self, request):
        """Tiny mobile-friendly control page: pick a match, see backend status."""
        return web.Response(text=ADMIN_HTML, content_type="text/html")

    async def h_state(self, request):
        return json_resp(self.state.overlay or {"ready": False})

    async def h_health(self, request):
        return json_resp({
            "ok": True,
            "status": self.state.status,
            "scraper": self.scraper.status,
            "assets": self.audio.assets(),
            "clients": len(self.clients),
            "match": {
                "id": self.scraper.match_id,
                "title": (self.state.snap or {}).get("title", ""),
                "url": "%s/live-cricket-scores/%s/live" % (CRICBUZZ, self.scraper.match_id),
            },
        })

    async def h_matches(self, request):
        try:
            if not self.scraper.session:
                await self.scraper.start()
            ms = await self.scraper.list_matches()
            return json_resp({"matches": ms})
        except Exception as exc:
            return json_resp({"error": str(exc), "matches": []}, 500)

    async def h_set_match(self, request):
        try:
            body = await request.json()
        except Exception:
            body = dict(request.query)
        mid = str(body.get("match_id") or "").strip()
        if not mid.isdigit():
            return json_resp({"error": "match_id must be numeric"}, 400)
        self.scraper.set_match(mid)
        self.state.over_events = []
        self.state.last_over_number = -1
        self.state.status.update({"match_id": mid, "balls_seen": 0})
        self.state.snap = None
        LOG.info("Match switched to %s", mid)
        return json_resp({"ok": True, "match_id": mid})

    async def h_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        LOG.info("Overlay connected (%d total)", len(self.clients))
        try:
            await ws.send_str(json.dumps({
                "type": "hello",
                "payload": {
                    "status": self.state.status,
                    "assets": self.audio.assets(),
                    "state": self.state.overlay,
                    "branding": self.branding,
                    "privacy": self.privacy,
                },
            }, ensure_ascii=False))
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        d = json.loads(msg.data)
                        if d.get("type") == "ping":
                            await ws.send_str(json.dumps({"type": "pong"}))
                    except Exception:
                        pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
            LOG.info("Overlay disconnected (%d left)", len(self.clients))
        return ws

    # -- aiohttp setup ------------------------------------------------------
    def make_app(self) -> web.Application:
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_get("/", self.h_index)
        app.router.add_get("/index.html", self.h_index)
        app.router.add_get("/ws", self.h_ws)
        app.router.add_get("/admin", self.h_admin)
        app.router.add_get("/api/state", self.h_state)
        app.router.add_get("/api/health", self.h_health)
        app.router.add_get("/api/matches", self.h_matches)
        app.router.add_post("/api/match", self.h_set_match)
        app.router.add_get("/api/match", self.h_set_match)
        app.router.add_static("/assets", ASSETS_DIR, show_index=True)
        app.router.add_static("/audio/tts", CACHE_DIR, show_index=False)
        app.router.add_static("/overlay", OVERLAY_DIR, show_index=True)
        return app

    async def run(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_running_loop().add_signal_handler(sig, self.stop_evt.set)
            except NotImplementedError:
                pass

        await self.audio.warmup()
        app = self.make_app()

        # playout buffer chalu karo (ab event loop chal raha hai)
        self.playout.start()

        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT") or self.args.port or self.cfg["server"].get("port", 8080))
        site = web.TCPSite(runner, self.cfg["server"].get("host", "0.0.0.0"), port)
        await site.start()
        LOG.info("=" * 68)
        LOG.info("  LIVE CRICKET 2D ENGINE  ->  http://0.0.0.0:%d", port)
        LOG.info("  overlay   : http://<host>:%d/", port)
        LOG.info("  matches   : http://<host>:%d/api/matches", port)
        LOG.info("  health    : http://<host>:%d/api/health", port)
        LOG.info("=" * 68)

        try:
            await self.live_loop()
        except asyncio.CancelledError:
            pass
        finally:
            await self.scraper.close()
            await runner.cleanup()


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Live Cricket 2D Engine backend")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--match", default=None, help="Cricbuzz match id (or 'auto')")
    ap.add_argument("--poll", type=float, default=None, help="seconds between scrapes")
    ap.add_argument("--rehearse", action="store_true", help="force the synthetic rehearsal feed")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config(args.config)
    asyncio.run(App(cfg, args).run())


if __name__ == "__main__":
    main()
