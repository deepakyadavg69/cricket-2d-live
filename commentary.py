"""
commentary.py — English commentary -> excited, radio-style Hindi lines.

Two engines:
  1. TEMPLATE (default, zero cost, zero latency)
     Rule-based Devanagari generation. Runs everywhere, never fails.
  2. LLM (optional)
     Set commentary.llm.enabled = true and drop GEMINI_API_KEY / OPENAI_API_KEY
     in the environment. The template line is used as the fallback if the API
     fails, times out, or isn't configured.

The overlay shows BOTH the Hindi line (big) and the English original (small),
so if the Hindi ever reads oddly the English is still on screen.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random

LOG = logging.getLogger("commentary")

# ---------------------------------------------------------------------------
#  Hindi building blocks
# ---------------------------------------------------------------------------
NAMES_HI = {
    "virat kohli": "विराट कोहली",
    "rohit sharma": "रोहित शर्मा",
    "ms dhoni": "एमएस धोनी",
    "kl rahul": "केएल राहुल",
    "jasprit bumrah": "जसप्रीत बुमराह",
    "hardik pandya": "हार्दिक पंड्या",
    "suryakumar yadav": "सूर्यकुमार यादव",
    "ravindra jadeja": "रविंद्र जडेजा",
    "shubman gill": "शुभमन गिल",
    "rishabh pant": "ऋषभ पंत",
    "mohammed shami": "मोहम्मद शमी",
    "kuldeep yadav": "कुलदीप यादव",
}

SHOT_HI = {
    "SHOT_SIX": "ज़बरदस्त शॉट",
    "SHOT_PULL": "पुल शॉट",
    "SHOT_HOOK": "हुक शॉट",
    "SHOT_SWEEP": "स्वीप शॉट",
    "SHOT_CUT": "कट शॉट",
    "SHOT_DRIVE": "शानदार ड्राइव",
    "SHOT_STRAIGHT": "स्ट्रेट ड्राइव",
    "SHOT_FLICK": "फ्लिक शॉट",
    "SHOT_DEFENSE": "शॉट",
    "UNKNOWN": "शॉट",
}

WICKET_HI = {
    "OUT_BOWLED": "बोल्ड",
    "OUT_CAUGHT": "कैच आउट",
    "OUT_RUNOUT": "रन आउट",
    "OUT_LBW": "एलबीडब्ल्यू",
    "OUT_STUMPED": "स्टंप आउट",
    "OUT_HITWICKET": "हिट विकेट",
}

INTRO = [
    "", "और ", "अब ", "यहाँ ", "लीजिए ", "देखिए ",
]
HYPE_SIX = ["क्या शॉट है!", "कमाल का शॉट!", "बेहतरीन!", "क्या बात है!"]
HYPE_FOUR = ["शानदार!", "खूबसूरत!", "क्या टाइमिंग है!", "बढ़िया शॉट!"]
HYPE_WICKET = ["बड़ा विकेट!", "बड़ा मोड़!", "क्या झटका!", "गेम चेंजर!"]


def _surname(name: str) -> str:
    """'VIRAT KOHLI' -> 'KOHLI'"""
    parts = (name or "").split()
    return parts[-1].title() if parts else ""


def hi_name(name: str) -> str:
    key = (name or "").lower().strip()
    if key in NAMES_HI:
        return NAMES_HI[key]
    return _surname(name) or "बल्लेबाज़"


def _pick(seq):
    return random.choice(seq)


# ---------------------------------------------------------------------------
#  Template engine
# ---------------------------------------------------------------------------
def template_line(cls: dict, ctx: dict) -> str:
    batter = hi_name(ctx.get("striker_name", ""))
    bowler = hi_name(ctx.get("bowler_name", ""))
    shot = SHOT_HI.get(cls.get("shot"), "शॉट")
    score = ctx.get("score", 0)
    wkts = ctx.get("wickets", 0)
    target = ctx.get("target", 0)
    need = ctx.get("need", 0)
    balls_left = ctx.get("balls_left", 0)
    pre = _pick(INTRO)

    # ---- WICKET -----------------------------------------------------------
    if cls.get("is_wicket"):
        wtype = WICKET_HI.get(cls.get("wicket_type"), "आउट")
        runs = ctx.get("striker_runs", 0)
        balls = ctx.get("striker_balls", 0)
        return (
            f"{pre}आउट!! {batter} {wtype} आउट, {runs} रन बनाकर पवेलियन लौटे। "
            f"{_pick(HYPE_WICKET)} टीम का स्कोर {score}/{wkts}।"
        )

    # ---- EXTRAS -----------------------------------------------------------
    et = cls.get("extra_type")
    if et == "WIDE":
        return f"{pre}वाइड! अंपायर ने बाहर करारा, टीम को मिला एक अतिरिक्त रन। स्कोर {score}/{wkts}।"
    if et == "NO_BALL":
        return f"{pre}नो बॉल! {bowler} का पैर आगे निकला, एक अतिरिक्त रन और फ्री हिट।"
    if et in ("BYE", "LEG_BYE"):
        return f"{pre}गेंद बल्ले को नहीं लगी, लेकिन एक रन मिल गया। स्कोर {score}/{wkts}।"

    # ---- SIX --------------------------------------------------------------
    if cls.get("is_six"):
        tail = ""
        if target:
            tail = f" अब {need} रन और चाहिए, {balls_left} गेंद बाकी।"
        return (
            f"{pre}छक्का!! {batter} ने {shot} खेला और गेंद स्टैंड्स में गायब! "
            f"{_pick(HYPE_SIX)}{tail}"
        )

    # ---- FOUR -------------------------------------------------------------
    if cls.get("is_four"):
        tail = ""
        if target:
            tail = f" स्कोर {score}/{wkts}, {need} रन {balls_left} गेंद में चाहिए।"
        return f"{pre}चौका! {batter} की {shot} से चार रन। {_pick(HYPE_FOUR)}{tail}"

    # ---- runs -------------------------------------------------------------
    ev = cls.get("event")
    if ev == "RUN_TRIPLE":
        return f"{pre}तीन रन! शानदार रनिंग, {batter} और साथी ने तीन रन पूरे किए। स्कोर {score}/{wkts}।"
    if ev == "RUN_DOUBLE":
        return f"{pre}दो रन! {batter} ने गेंद को गैप में डाला और दो रन ले लिए। स्कोर {score}/{wkts}।"
    if ev == "RUN_SINGLE":
        return f"{pre}एक रन, {batter} ने सिंगल लिया और स्ट्राइक अपने पास रखी।"
    if ev == "RUN_FIVE":
        return f"{pre}पाँच रन! ओवरथ्रो से मिले अतिरिक्त रन। स्कोर {score}/{wkts}।"

    # ---- dot --------------------------------------------------------------
    dots = [
        f"{pre}कोई रन नहीं, {bowler} की शानदार गेंद, {batter} ने सम्हल कर खेला।",
        f"{pre}डॉट बॉल! दबाव बढ़ रहा है, {bowler} शानदार गेंदबाज़ी कर रहे हैं।",
        f"{pre}रन नहीं मिला। {bowler} बनाए हुए हैं दबाव, स्कोर {score}/{wkts}।",
    ]
    return _pick(dots)


def milestone_line(ms: str, ctx: dict) -> str:
    who = hi_name(ctx.get("striker_name", ""))
    if ms == "FIFTY":
        return f"अर्धशतक! {who} ने अपना फिफ्टी पूरा किया, शानदार पारी! स्टेडियम तालियों से गूँज उठा।"
    if ms == "HUNDRED":
        return f"शतक!! {who} ने जड़ दिया शतक! क्या पारी है, बल्ले से आग लग रही है!"
    if ms == "HAT_TRICK":
        return "हैट-ट्रिक!! तीन गेंदों में तीन विकेट, इतिहास रच दिया!"
    if ms == "FIVE_FOR":
        return f"पाँच विकेट! {hi_name(ctx.get('bowler_name',''))} ने अपना फाइफर पूरा किया, कमाल की गेंदबाज़ी!"
    return ""


def over_summary_line(over_no: int, runs: int, timeline: list, ctx: dict) -> str:
    """~10 second Hindi breakdown, synthesised at the end of every over."""
    bowler = hi_name(ctx.get("bowler_name", ""))
    b1 = hi_name(ctx.get("striker_name", ""))
    b2 = hi_name(ctx.get("non_striker_name", ""))
    seq = " ".join(str(t) for t in timeline) if timeline else "—"
    fours = sum(1 for t in timeline if str(t) == "4")
    sixes = sum(1 for t in timeline if str(t) == "6")
    wkts = sum(1 for t in timeline if str(t).upper() == "W")

    flavor = "धमाकेदार ओवर" if runs >= 15 else (
        "बढ़िया ओवर" if runs >= 9 else (
        "किफ़ायती ओवर" if runs <= 4 else "संतुलित ओवर"))
    extra = []
    if fours:
        extra.append(f"{fours} चौके")
    if sixes:
        extra.append(f"{sixes} छक्के")
    if wkts:
        extra.append(f"{wkts} विकेट")
    boundary_txt = (" इस ओवर में " + " और ".join(extra) + " शामिल रहे।") if extra else ""

    score = ctx.get("score", 0)
    wk_total = ctx.get("wickets", 0)
    target = ctx.get("target", 0)
    need = ctx.get("need", 0)
    balls_left = ctx.get("balls_left", 0)
    chase = f" अब {need} रन {balls_left} गेंद में चाहिए।" if target else ""

    return (
        f"ओवर {over_no} का हाल: {runs} रन, {flavor}। "
        f"इस ओवर की हर गेंद: {seq}।{boundary_txt} "
        f"{bowler} की गेंदबाज़ी में स्कोर अब {score}/{wk_total}। "
        f"क्रीज़ पर {b1} और {b2} मौजूद हैं।{chase}"
    )


# ---------------------------------------------------------------------------
#  Optional LLM engine
# ---------------------------------------------------------------------------
class CommentaryEngine:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.mode = (cfg.get("mode") or "template").lower()
        self.provider = (cfg.get("provider") or "gemini").lower()
        self.model = cfg.get("model") or "gemini-2.0-flash"
        self.api_key_env = cfg.get("api_key_env") or "GEMINI_API_KEY"
        self.temperature = float(cfg.get("temperature") or 0.85)
        self.timeout = float(cfg.get("timeout") or 4.0)
        self.enabled = bool(cfg.get("enabled"))

        self._key = os.getenv(self.api_key_env, "")
        self.llm_on = self.enabled and self.mode == "llm" and bool(self._key)
        if self.enabled and not self._key:
            LOG.warning("LLM commentary enabled but %s is not set -> using templates", self.api_key_env)
        LOG.info("Commentary engine: %s", "LLM (%s)" % self.provider if self.llm_on else "template")

    async def ball_line(self, cls: dict, ctx: dict, text_en: str) -> str:
        base = template_line(cls, ctx)
        ms = cls.get("milestone")
        if ms:
            line = milestone_line(ms, ctx)
            if line:
                base = line + " " + base
        if not self.llm_on:
            return base

        prompt = (
            "You are an excitable Hindi cricket radio commentator on a live YouTube stream.\n"
            "Convert the English ball-by-ball commentary into ONE punchy Hindi sentence "
            "(max 28 words, Devanagari script, no English words except player names, "
            "no emoji, no hashtags, no markdown). Keep it family friendly.\n\n"
            f"English: {text_en}\n"
            f"Runs: {ctx.get('runs')}  Wicket: {cls.get('is_wicket')}  "
            f"Score: {ctx.get('score')}/{ctx.get('wickets')}\n"
            f"Striker: {ctx.get('striker_name')}  Bowler: {ctx.get('bowler_name')}\n\n"
            "Hindi line:"
        )
        try:
            out = await asyncio.wait_for(self._call_llm(prompt), timeout=self.timeout)
            out = (out or "").strip().strip('"').strip()
            return out if out else base
        except Exception as exc:
            LOG.warning("LLM commentary failed (%s) -> template fallback", exc)
            return base

    async def _call_llm(self, prompt: str) -> str:
        import aiohttp

        if self.provider == "gemini":
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
                % (self.model, self._key)
            )
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": self.temperature, "maxOutputTokens": 120},
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=self.timeout)) as r:
                    d = await r.json()
                    return d["candidates"][0]["content"]["parts"][0]["text"]
        # OpenAI-compatible (OpenAI / Groq / Together / local)
        url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": 120,
        }
        headers = {"Authorization": "Bearer " + self._key}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=self.timeout)) as r:
                d = await r.json()
                return d["choices"][0]["message"]["content"]

    async def over_line(self, over_no: int, runs: int, timeline: list, ctx: dict) -> str:
        return over_summary_line(over_no, runs, timeline, ctx)


if __name__ == "__main__":
    from classifier import classify

    eng = CommentaryEngine({})
    ctx = {
        "striker_name": "VIRAT KOHLI", "bowler_name": "MITCHELL STARC",
        "score": 148, "wickets": 4, "target": 195, "need": 47, "balls_left": 22,
        "striker_runs": 62, "striker_balls": 38, "non_striker_name": "KL RAHUL",
    }
    demo = [
        ("Lofted over long-on, SIX!", 6, False),
        ("Driven through covers for FOUR", 4, False),
        ("Defended off the back foot", 0, False),
        ("OUT! Bowled! Stumps shattered", 0, True),
        ("Flicked off the pads, single taken", 1, False),
    ]
    for txt, r, w in demo:
        c = classify(txt, r, w)
        ctx["runs"] = r
        print(asyncio.run(eng.ball_line(c, ctx, txt)))
    print()
    print(asyncio.run(eng.over_line(15, 12, ["1", "4", "0", "6", "W", "2"], ctx)))
