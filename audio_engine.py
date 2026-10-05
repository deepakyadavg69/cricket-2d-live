"""
audio_engine.py — Hindi TTS + asset management.

IMPORTANT DESIGN NOTE (this is why there is no python-vlc / ffmpeg / VB-CABLE)
-----------------------------------------------------------------------------
The original design played audio on the *server*. On a cloud host that is
useless — nobody hears the server's speakers.

Instead this engine only **produces MP3 files** and hands the browser a URL:

    edge-tts  ->  cache/tts/<hash>.mp3  ->  served at GET /audio/tts/<hash>.mp3

The overlay's <audio> element plays it, and the Web Audio graph in the browser
does the BGM ducking (100% -> 20%) around it. That means:

    * no audio driver, no sound card, no virtual cable on the server
    * Prism Live's "internal audio" capture picks up everything in one shot
    * the whole thing works on Render / Koyeb / Railway free tiers

edge-tts is free (Microsoft Edge's neural endpoint) and needs no API key.
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import logging
import os
import time
from typing import Optional

LOG = logging.getLogger("audio")

try:
    import edge_tts  # type: ignore

    HAS_EDGE_TTS = True
except Exception:  # pragma: no cover
    HAS_EDGE_TTS = False
    LOG.warning("edge-tts not installed -> Hindi voice commentary disabled "
                "(browser will fall back to device TTS). pip install edge-tts")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "cache", "tts")
ASSETS_DIR = os.path.join(ROOT, "assets")
BGM_DIR = os.path.join(ASSETS_DIR, "bgm")
SFX_DIR = os.path.join(ASSETS_DIR, "sfx")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(BGM_DIR, exist_ok=True)
os.makedirs(SFX_DIR, exist_ok=True)

# Files older than this are deleted on cleanup
CACHE_TTL = 6 * 3600
MAX_CACHE_FILES = 400


class AudioEngine:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True)) and HAS_EDGE_TTS
        self.voice = cfg.get("voice") or "hi-IN-MadhurNeural"
        self.voice_over = cfg.get("voice_over") or self.voice
        self.rate = cfg.get("rate") or "+12%"
        self.rate_over = cfg.get("rate_over") or "+8%"
        self.pitch = cfg.get("pitch") or "+6Hz"
        self.volume = cfg.get("volume") or "+0%"
        self.timeout = float(cfg.get("timeout") or 12.0)
        self._lock = asyncio.Lock()
        self._last_cleanup = 0.0

        LOG.info("Audio engine: %s | voice=%s rate=%s",
                 "edge-tts" if self.enabled else "DISABLED", self.voice, self.rate)

    # ------------------------------------------------------------------
    def _path_for(self, text: str, over: bool = False,
                  rate: str = None, pitch: str = None) -> tuple:
        # per-call prosody (six = fast & high, wicket = slower & low) — the
        # cache key must include it or every six would reuse the dot-ball take.
        key = "|".join([
            text.strip(),
            self.voice_over if over else self.voice,
            rate or (self.rate_over if over else self.rate),
            pitch or self.pitch,
            self.volume,
        ]).encode("utf-8")
        h = hashlib.sha1(key).hexdigest()[:20]
        return os.path.join(CACHE_DIR, h + ".mp3"), "/audio/tts/" + h + ".mp3"

    async def synth(self, text: str, over: bool = False,
                    rate: str = None, pitch: str = None) -> Optional[str]:
        """
        Synthesise `text` to MP3 (cached). Returns the URL path, or None when
        TTS is unavailable — the overlay degrades gracefully in that case.

        rate/pitch: optional per-delivery prosody, e.g. "+24%" / "+16Hz".
        """
        if not text or not text.strip():
            return None
        if not self.enabled:
            return None

        path, url = self._path_for(text, over, rate, pitch)
        if os.path.exists(path) and os.path.getsize(path) > 900:
            return url

        async with self._lock:
            if os.path.exists(path) and os.path.getsize(path) > 900:
                return url
            try:
                await asyncio.wait_for(
                    self._synth_to(text, path, over, rate, pitch), timeout=self.timeout)
            except asyncio.TimeoutError:
                LOG.warning("TTS timeout for: %.40s…", text)
                return None
            except Exception as exc:
                LOG.warning("TTS failed (%s) for: %.40s…", exc, text)
                return None

        if os.path.exists(path) and os.path.getsize(path) > 900:
            self._maybe_cleanup()
            return url
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    async def _synth_to(self, text: str, path: str, over: bool,
                        rate: str = None, pitch: str = None):
        comm = edge_tts.Communicate(
            text=text,
            voice=(self.voice_over if over else self.voice),
            rate=rate or (self.rate_over if over else self.rate),
            pitch=pitch or self.pitch,
            volume=self.volume,
        )
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    f.write(chunk["data"])
        os.replace(tmp, path)
        LOG.debug("TTS -> %s (%d bytes)", os.path.basename(path), os.path.getsize(path))

    # ------------------------------------------------------------------
    def _maybe_cleanup(self):
        now = time.time()
        if now - self._last_cleanup < 600:
            return
        self._last_cleanup = now
        files = glob.glob(os.path.join(CACHE_DIR, "*.mp3"))
        files.sort(key=os.path.getmtime)
        for f in files:
            if now - os.path.getmtime(f) > CACHE_TTL:
                try:
                    os.remove(f)
                except OSError:
                    pass
        files = glob.glob(os.path.join(CACHE_DIR, "*.mp3"))
        if len(files) > MAX_CACHE_FILES:
            files.sort(key=os.path.getmtime)
            for f in files[: len(files) - MAX_CACHE_FILES]:
                try:
                    os.remove(f)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    #  BGM / SFX assets
    # ------------------------------------------------------------------
    def assets(self) -> dict:
        """What the browser can actually load — the overlay falls back to
        WebAudio synthesis for any SFX that isn't present."""
        bgm = sorted(
            [
                "/assets/bgm/" + os.path.basename(p)
                for p in glob.glob(os.path.join(BGM_DIR, "*.mp3"))
                + glob.glob(os.path.join(BGM_DIR, "*.ogg"))
                + glob.glob(os.path.join(BGM_DIR, "*.wav"))
            ]
        )
        sfx = {}
        for p in glob.glob(os.path.join(SFX_DIR, "*.mp3")) + glob.glob(os.path.join(SFX_DIR, "*.wav")) + glob.glob(os.path.join(SFX_DIR, "*.ogg")):
            name = os.path.splitext(os.path.basename(p))[0].lower()
            sfx[name] = "/assets/sfx/" + os.path.basename(p)
        return {"bgm": bgm, "sfx": sfx, "tts": self.enabled}

    async def warmup(self):
        """Pre-generate the small set of lines that repeat every match, so the
        very first ball isn't waiting on the network."""
        if not self.enabled:
            return
        warm = [
            "नमस्कार दोस्तों, आप देख रहे हैं लाइव क्रिकेट!",
            "चौका! शानदार शॉट।",
            "छक्का! गेंद स्टैंड्स में गायब!",
            "आउट! बड़ा विकेट।",
        ]
        for t in warm:
            await self.synth(t)
        LOG.info("TTS warmup done")


# Expected (optional) asset filenames; purely advisory for the README/health check
EXPECTED_SFX = ["four", "six", "wicket", "cheer", "whistle", "bat"]


if __name__ == "__main__":
    async def _demo():
        eng = AudioEngine({})
        print("assets:", eng.assets())
        url = await eng.synth("नमस्कार दोस्तों! विराट कोहली ने छक्का जड़ दिया!")
        print("synth ->", url)

    asyncio.run(_demo())
