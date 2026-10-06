#!/usr/bin/env python3
"""
telegram_bot.py — phone se poora control, aur khud-ba-khud khabar.

KYUN: tablet ghar par ho aur aap bahar ho, tab bhi match badal sako,
score dekh sako — aur koi dikkat aaye to bot khud message bhej de.

COMMANDS
  /start      — bot jagao, webhook set karo
  /help       — sab commands
  /matches    — abhi live matches ki list
  /set <id>   — match badlo (jaise: /set 173711)
  /score      — abhi ka score
  /status     — server health (scraper, tts, delay, errors)
  /rehearse   — practice mode (bina live match ke)
  /live       — live match par wapas jao
  /notify on|off  — khabar band/chalu
  /ping       — server jagao

SETTINGS
  config.yaml me:
    telegram:
      bot_token: "..."
      owner_chat_id: "..."
      enabled: true
  Ya environment: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import aiohttp

LOG = logging.getLogger("telegram")

API = "https://api.telegram.org/bot%s/%s"
HTTP_TIMEOUT = 12


class TelegramBot:
    """Bahut halka bot — sirf webhook, koi polling nahi (Render free ke liye)."""

    def __init__(self, token: str = "", chat_id: str = "", webhook_base: str = ""):
        self.token = (token or os.getenv("TELEGRAM_TOKEN") or "").strip()
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
        self.webhook_base = (webhook_base or "").rstrip("/")
        self.enabled = bool(self.token and self.chat_id)
        self.notify = True                # /notify off se band hota hai
        self._sess: Optional[aiohttp.ClientSession] = None
        self._last_send = 0.0
        self._min_gap = 1.2               # Telegram spam limit se bachav
        self._last_err_notify = 0.0
        self._err_streak = 0
        self.app = None                   # main.App ka ref, commands ke liye
        self.started_at = time.time()

        if self.enabled:
            LOG.info("Telegram bot ON (chat %s)", self.chat_id)
        else:
            LOG.info("Telegram bot off — token/chat_id nahi mila")

    # ------------------------------------------------------------ plumbing --
    async def _session(self):
        if self._sess is None or self._sess.closed:
            self._sess = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT))
        return self._sess

    async def close(self):
        if self._sess and not self._sess.closed:
            await self._sess.close()

    async def api(self, method: str, **params) -> Optional[dict]:
        """Telegram API call. Fail ho to chupke se None (server kabhi na march)."""
        if not self.token:
            return None
        try:
            s = await self._session()
            async with s.post(API % (self.token, method), json=params) as r:
                d = await r.json(content_type=None)
                return d if isinstance(d, dict) else None
        except Exception as exc:
            LOG.debug("telegram %s fail: %s", method, exc)
            return None

    async def send(self, text: str, chat_id: str = "", silent: bool = False,
                   force: bool = False):
        """Message bhejo. Thoda gap rakhte hain taiki Telegram rate-limit na kare."""
        if not self.enabled or (not self.notify and not force):
            return False
        cid = str(chat_id or self.chat_id)
        if not cid:
            return False

        gap = time.time() - self._last_send
        if gap < self._min_gap:
            await asyncio.sleep(self._min_gap - gap)

        ok = await self.api("sendMessage", chat_id=cid, text=text[:4000],
                            disable_notification=silent,
                            disable_web_page_preview=True)
        self._last_send = time.time()
        if not ok or not ok.get("ok"):
            LOG.debug("telegram send fail: %s", (ok or {}).get("description"))
            return False
        return True

    # ------------------------------------------------------------- webhook --
    async def set_webhook(self) -> bool:
        """Bot ko batao ki updates kahan bhejne hain."""
        if not self.enabled or not self.webhook_base:
            return False
        url = self.webhook_base + "/telegram"
        r = await self.api("setWebhook", url=url,
                           allowed_updates=["message", "callback_query"],
                           drop_pending_updates=True)
        if r and r.get("ok"):
            LOG.info("Telegram webhook set: %s", url)
            return True
        LOG.warning("Telegram webhook set nahi hua: %s", (r or {}).get("description"))
        return False

    async def handle(self, payload: dict):
        """Telegram se aaya update. Kabhi exception bahar nahi jane dena."""
        if not self.enabled:
            return                      # bot band -> bilkul kuch mat karo
        try:
            msg = payload.get("message") or payload.get("edited_message") or {}
            if not msg:
                return
            chat = msg.get("chat", {})
            cid = str(chat.get("id", ""))
            text = (msg.get("text") or "").strip()

            # sirf malik ke liye
            if cid != self.chat_id:
                LOG.warning("Telegram: anjaan chat %s — ignore", cid)
                await self.api("sendMessage", chat_id=cid,
                               text="⛔ Ye bot private hai.")
                return

            if not text:
                return
            await self._command(text.lower(), cid, chat)
        except Exception as exc:
            LOG.exception("telegram handle: %s", exc)

    async def _command(self, raw: str, cid: str, chat: dict):
        parts = raw.split()
        cmd = parts[0].split("@")[0]
        arg = " ".join(parts[1:]).strip()
        app = self.app

        if cmd in ("/start", "/help"):
            await self.send(
                "🏏 SPORTS GYAN — Control Bot\n\n"
                "/matches          live matches ki list\n"
                "/set <id>         match badlo  (jaise /set 173711)\n"
                "/score            abhi ka score\n"
                "/status           server health\n"
                "/rehearse         practice mode\n"
                "/live             live par wapas\n"
                "/notify on|off    khabar band / chalu\n"
                "/mobile           phone se live karne ka page\n"
                "/link             saare links\n"
                "/preview          layout guide\n"
                "/ping             server jagao\n\n"
                "Koi bhi dikkat aaye to main khud yahan bata doonga. 🙂",
                cid, force=True)
            return

        if cmd == "/ping":
            await self.send("🏓 Pong! Server jag raha hai.", cid, force=True)
            return

        if cmd == "/mobile":
            base = (self.webhook_base or "").rstrip("/")
            await self.send(
                "📱 MOBILE CONTROL\n\n"
                "Phone ke Chrome me (Desktop site ON) kholein:\n"
                + base + "/m\n\n"
                "Wahan se match chuno, phir OVERLAY KHOLEN dabayein.\n"
                "Overlay khulne ke baad ek baar screen par TAP karein "
                "— awaaz tabhi chalu hoti hai.\n\n"
                "Overlay direct: " + base + "/?auto=1", cid, force=True)
            return

        if cmd == "/preview":
            base = (self.webhook_base or "").rstrip("/")
            await self.send(
                "🖼 Layout guide: " + base + "/preview\n"
                "Admin: " + base + "/admin", cid, force=True)
            return

        if cmd == "/link":
            base = (self.webhook_base or "").rstrip("/")
            await self.send(
                "🔗 " + base + "/m        (mobile control)\n"
                "🔗 " + base + "/?auto=1  (overlay)\n"
                "🔗 " + base + "/admin    (admin)\n"
                "🔗 " + base + "/api/health", cid, force=True)
            return

        if cmd == "/notify":
            if arg == "off":
                self.notify = False
                await self.send("🔇 Khabar band. (/notify on se wapas chalu karo)",
                                cid, force=True)
            else:
                self.notify = True
                await self.send("🔔 Khabar chalu.", cid, force=True)
            return

        if cmd == "/status":
            if not app:
                await self.send("⚠️ Server abhi ready nahi hai.", cid, force=True)
                return
            s = app.state.status
            up = int(time.time() - self.started_at)
            await self.send(
                "🖥 SERVER STATUS\n\n"
                f"• Mode      : {s.get('mode')}\n"
                f"• Scraper   : {s.get('scraper')}\n"
                f"• Match     : {s.get('title') or '—'}\n"
                f"• Match ID  : {s.get('match_id') or '—'}\n"
                f"• Balls     : {s.get('balls_seen', 0)}\n"
                f"• TTS       : {'haan' if s.get('tts') else 'nahi'}\n"
                f"• Errors    : {s.get('errors', 0)}\n"
                f"• Delay     : {app.playout.delay:.0f}s\n"
                f"• Uptime    : {up//3600}h {(up%3600)//60}m\n"
                f"• Viewers   : {len(app.clients)}",
                cid, force=True)
            return

        if cmd == "/score":
            snap = app.state.snap if app else None
            if not snap:
                await self.send("⚠️ Abhi koi score nahi hai.", cid, force=True)
                return
            bat = snap.get("batting", {}) or {}
            st = snap.get("striker", {}) or {}
            ns = snap.get("non_striker", {}) or {}
            bw = snap.get("bowler", {}) or {}
            line = (f"🏏 {bat.get('code','?')} {snap.get('score',0)}/{snap.get('wickets',0)}"
                    f"  ({snap.get('overs','0.0')} ov)\n\n"
                    f"• {st.get('name','—')}  {st.get('runs',0)}({st.get('balls',0)})  ⬅️ strike\n"
                    f"• {ns.get('name','—')}  {ns.get('runs',0)}({ns.get('balls',0)})\n"
                    f"• {bw.get('name','—')}  {bw.get('overs',0)}-{bw.get('maidens',0)}-"
                    f"{bw.get('runs',0)}-{bw.get('wickets',0)}\n")
            if snap.get("target"):
                line += (f"\n🎯 Target {snap['target']} · "
                         f"{snap.get('need',0)} run {snap.get('balls_left',0)} ball me\n"
                         f"CRR {snap.get('crr',0)} · RRR {snap.get('rrr',0)}")
            await self.send(line, cid, force=True)
            return

        if cmd == "/matches":
            if not app:
                await self.send("⚠️ Server ready nahi hai.", cid, force=True)
                return
            try:
                from scraper import list_matches
                ms = await list_matches(app.scraper.session)
            except Exception as exc:
                LOG.debug("list_matches: %s", exc)
                ms = []
            if not ms:
                await self.send("😴 Abhi koi match nahi mila.", cid, force=True)
                return
            out = "📋 MATCHES\n\n"
            for m in ms[:14]:
                flag = "🟢" if (m.get("state") or "").lower().startswith("in progress") else "⚪"
                out += (f"{flag} {m.get('title','?')[:38]}\n"
                        f"   /set_{m.get('match_id')}  · {m.get('format','')} · {m.get('state','')}\n")
            await self.send(out, cid, force=True)
            return

        if cmd.startswith("/set") and cmd != "/set":
            # /set_173711  (list me tap karne ke liye)
            arg = cmd[5:]
        if cmd == "/set" or arg:
            mid = "".join(ch for ch in (arg or "") if ch.isdigit())
            if not mid:
                await self.send("❌ Sahi tarika: /set 173711", cid, force=True)
                return
            if not app:
                await self.send("⚠️ Server ready nahi hai.", cid, force=True)
                return
            app.scraper.set_match(mid)
            app.state.over_events = []
            app.state.last_over_number = -1
            app.state.status.update({"match_id": mid, "balls_seen": 0})
            app.state.snap = None
            await self.send(f"✅ Match set: {mid}\nServer agle poll par is par jayega.",
                            cid, force=True)
            return

        if cmd == "/rehearse":
            if app:
                app.args.rehearse = True
                app.want_rehearsal = True
            await self.send("🎬 Rehearsal mode. (/live se wapas live par jao)",
                            cid, force=True)
            return

        if cmd == "/live":
            if app:
                app.args.rehearse = False
                app.want_live = True
            await self.send("🔴 Live match dhoondh rahe hain…", cid, force=True)
            return

        await self.send("🤔 Samajh nahi aaya. /help dekho.", cid, force=True)

    # --------------------------------------------------------- khabar (auto) --
    async def notify_error(self, where: str, exc):
        """Error aaye to batao — par har chhoti baat par nahi (streak logic)."""
        self._err_streak += 1
        now = time.time()
        # pehli galti turant, uske baad 10 minute me ek hi baar
        if self._err_streak > 1 and (now - self._last_err_notify) < 600:
            return
        self._last_err_notify = now
        await self.send(
            f"⚠️ DIKKAT: {where}\n\n{type(exc).__name__}: {exc}\n\n"
            f"(streak {self._err_streak}) — main dobara try kar raha hoon.")

    async def notify_recovered(self, what: str):
        if self._err_streak:
            self._err_streak = 0
            await self.send(f"✅ Theek ho gaya: {what}")

    async def notify_match(self, title: str, mid: str, live: bool = True):
        await self.send(
            ("🔴 LIVE MATCH\n\n" if live else "🎬 REHEARSAL\n\n") +
            f"{title}\nID: {mid}",
            silent=not live)

    async def notify_milestone(self, kind: str, label: str, who: str):
        await self.send(f"⭐ {label} — {who}", silent=True)

    async def notify_scraper_down(self):
        await self.send(
            "📡 Cricbuzz se jawab nahi aa raha (3 minute).\n"
            "Stream rukne nahi di — abhi rehearsal chal raha hai, "
            "live match milte hi wapas aa jayenge.")
