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
# ---------------------------------------------------------------------------
#  Player names in Devanagari.
#
#  NOTE ON TRANSLITERATION: an algorithmic English->Devanagari romaniser was
#  tested and rejected - it produced "Kohli -> कोह्ली" and "Cummins -> उम्मिन्स"
#  (it ate the leading C). Wrong names sound far worse on air than Latin ones,
#  so we hand-curate the players that actually matter and leave everyone else
#  in Latin. edge-tts reads Latin names with a Hindi accent, which is exactly
#  how real Hindi commentary sounds for overseas players.
# ---------------------------------------------------------------------------
NAMES_HI = {
    # --- India ---
    "virat kohli": "विराट कोहली", "rohit sharma": "रोहित शर्मा",
    "ms dhoni": "महेंद्र सिंह धोनी", "kl rahul": "केएल राहुल",
    "jasprit bumrah": "जसप्रीत बुमराह", "hardik pandya": "हार्दिक पंड्या",
    "suryakumar yadav": "सूर्यकुमार यादव", "ravindra jadeja": "रवींद्र जडेजा",
    "shubman gill": "शुभमन गिल", "rishabh pant": "ऋषभ पंत",
    "mohammed shami": "मोहम्मद शमी", "kuldeep yadav": "कुलदीप यादव",
    "ravichandran ashwin": "रविचंद्रन अश्विन", "shreyas iyer": "श्रेयस अय्यर",
    "ishan kishan": "ईशान किशन", "yuzvendra chahal": "युजवेंद्र चहल",
    "bhuvneshwar kumar": "भुवनेश्वर कुमार", "mohammed siraj": "मोहम्मद सिराज",
    "arshdeep singh": "अर्शदीप सिंह", "axar patel": "अक्षर पटेल",
    "washington sundar": "वॉशिंगटन सुंदर", "tilak varma": "तिलक वर्मा",
    "ruturaj gaikwad": "रुतुराज गायकवाड", "sanju samson": "संजू सैमसन",
    "shivam dube": "शिवम दुबे", "riyan parag": "रियान पराग",
    "rinku singh": "रिंकू सिंह", "abhishek sharma": "अभिषेक शर्मा",
    "nitish kumar reddy": "नितीश कुमार रेड्डी", "harshit rana": "हर्षित राणा",
    "yashasvi jaiswal": "यशस्वी जयसवाल", "sarfaraz khan": "सरफराज खान",
    "dhruv jurel": "ध्रुव जुरेल", "shardul thakur": "शार्दुल ठाकुर",
    # --- overseas ---
    "steve smith": "स्टीव स्मिथ", "david warner": "डेविड वॉर्नर",
    "mitchell starc": "मिशेल स्टार्क", "pat cummins": "पैट कमिंस",
    "josh hazlewood": "जॉश हेज़लवुड", "glenn maxwell": "ग्लेन मैक्सवेल",
    "travis head": "ट्रैविस हेड", "marnus labuschagne": "मार्नस लाबुशेन",
    "joe root": "जो रूट", "ben stokes": "बेन स्टोक्स",
    "jos buttler": "जोस बटलर", "jofra archer": "जोफ्रा आर्चर",
    "kane williamson": "केन विलियमसन", "trent boult": "ट्रेंट बोल्ट",
    "rachin ravindra": "रचिन रवींद्र", "kagiso rabada": "कगिसो रबाडा",
    "quinton de kock": "क्विंटन डी कॉक", "heinrich klaasen": "हेनरिक क्लासेन",
    "shaheen afridi": "शाहीन अफरीदी", "babar azam": "बाबर आज़म",
    "shakib al hasan": "शाकिब अल हसन", "rashid khan": "राशिद खान",
    "andre russell": "आंद्रे रसेल", "sunil narine": "सुनील नरेन",
    "faf du plessis": "फाफ डु प्लेसिस", "ab de villiers": "एबी डिविलियर्स",
    "chris gayle": "क्रिस गेल", "kieron pollard": "किरोन पोलार्ड",
    "dwayne bravo": "ड्वेन ब्रावो", "sikandar raza": "सिकंदर रज़ा",
}

# surname-only fallback (so "V KOHLI" / "VIRAT KOHLI" dono chal jaayein)
SURNAME_HI = {
    "kohli": "कोहली", "sharma": "शर्मा", "dhoni": "धोनी", "rahul": "राहुल",
    "bumrah": "बुमराह", "pandya": "पंड्या", "yadav": "यादव", "jadeja": "जडेजा",
    "gill": "गिल", "pant": "पंत", "shami": "शमी", "kuldeep": "कुलदीप",
    "ashwin": "अश्विन", "iyer": "अय्यर", "kishan": "किशन", "chahal": "चहल",
    "kumar": "कुमार", "siraj": "सिराज", "singh": "सिंह", "patel": "पटेल",
    "sundar": "सुंदर", "varma": "वर्मा", "gaikwad": "गायकवाड", "samson": "सैमसन",
    "dube": "दुबे", "parag": "पराग", "rinku": "रिंकू", "abhishek": "अभिषेक",
    "reddy": "रेड्डी", "rana": "राणा", "jaiswal": "जयसवाल", "khan": "खान",
    "jurel": "जुरेल", "thakur": "ठाकुर", "smith": "स्मिथ", "warner": "वॉर्नर",
    "starc": "स्टार्क", "cummins": "कमिंस", "hazlewood": "हेज़लवुड",
    "maxwell": "मैक्सवेल", "head": "हेड", "labuschagne": "लाबुशेन",
    "root": "रूट", "stokes": "स्टोक्स", "buttler": "बटलर", "archer": "आर्चर",
    "williamson": "विलियमसन", "boult": "बोल्ट", "ravindra": "रवींद्र",
    "rabada": "रबाडा", "kock": "डी कॉक", "klaasen": "क्लासेन",
    "afridi": "अफरीदी", "azam": "आज़म", "hasan": "अल हसन", "russell": "रसेल",
    "narine": "नरेन", "plessis": "डु प्लेसिस", "villiers": "डिविलियर्स",
    "gayle": "गेल", "pollard": "पोलार्ड", "bravo": "ब्रावो", "raza": "रज़ा",
}

# TTS prosody per event — makes the voice sound like a real commentator
# instead of a flat robot reading the same sentence all match.
PROSODY = {
    "RUN_SIX":   {"rate": "+24%", "pitch": "+16Hz"},
    "RUN_FOUR":  {"rate": "+18%", "pitch": "+11Hz"},
    "WICKET":    {"rate": "+10%", "pitch": "-6Hz"},
    "MILESTONE": {"rate": "+22%", "pitch": "+18Hz"},
    "DOT_BALL":  {"rate": "+8%",  "pitch": "+2Hz"},
    "RUN_SINGLE":{"rate": "+12%", "pitch": "+6Hz"},
    "RUN_DOUBLE":{"rate": "+14%", "pitch": "+8Hz"},
    "RUN_TRIPLE":{"rate": "+16%", "pitch": "+9Hz"},
    "_over":     {"rate": "+6%",  "pitch": "+2Hz"},
}
DEFAULT_PROSODY = {"rate": "+12%", "pitch": "+6Hz"}


def prosody_for(cls: dict, over: bool = False) -> dict:
    if over:
        return PROSODY["_over"]
    if cls.get("milestone"):
        return PROSODY["MILESTONE"]
    return PROSODY.get(cls.get("event", ""), DEFAULT_PROSODY)


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

# ---------------------------------------------------------------------------
#  Akash Chopra "andaaaz" — tez, joshile, chhote tukde.
#
#  DHYAAN RAKHEIN: ye hamari apni Hindi hai, Chopra sahab ki awaaz ya unka
#  koi bola hua jumla nahi. Sirf unki commentary ka *style* — chhote sentence,
#  "dekhiye!", "bawaal!", seedha chakka!" wala energy. Awaaz alag, feel waisi.
#  (Kisi asli insan ki awaaz ki nakal karna kanooni pareshani hai — hum woh
#   nahi karte. Isse 100% apna hai.)
# ---------------------------------------------------------------------------
LEAD = ["", "", "", "और ", "अब ", "यहाँ ", "लीजिए ", "देखिए ", "अरे! ", "हाँ! ", "सुनिए! ", "ओहो! "]

SIX_CALL = [
    "छक्का!! {b} ने {s} खेला और गेंद स्टैंड्स में गायब!",
    "चक्का!! {b} ने बस हाथ घुमाया और गेंद सीमा पार!",
    "और ये उड़ गयी!! {b} का {s}, गेंद हवा में और बाहर!",
    "छह रन! {b} — एक झटका और गेंद गायब!",
    "बवाल! {b} ने {s} मारी, गेंद जा गिरी दर्शकों में!",
    "देखिए! {b} की करारी {s}, गेंद हवा में — और छक्का!",
    "क्या शॉट है! {b} ने {s} से भेज दिया स्टैंड्स में!",
    "सीधा छक्का! {b} ने {s} खेली और गेंद पार!",
]
SIX_AFTER = ["क्या बात है!", "बहुत बढ़िया!", "कमाल का शॉट!", "बवाल!",
             "क्या टाइमिंग है!", "एकदम साफ़!", "ज़बरदस्त!", "क्या मारा है!"]

FOUR_CALL = [
    "चौका! {b} की {s}, चार रन!",
    "और चार! {b} ने {s} से गेंद भेजी बाउंड्री पर!",
    "चौका!! {b} ने खूबसूरती से {s} खेली!",
    "देखिए! {b} का {s} — और गेंद दौड़ गयी बाउंड्री तक!",
    "चार रन! {b} ने गेंद गैप में डाली, चौका!",
    "क्या शॉट! {b} की {s}, और चौका!",
    "और ये चौका! {b} ने {s} से चार रन!",
]
FOUR_AFTER = ["शानदार!", "खूबसूरत!", "क्या टाइमिंग है!", "बढ़िया!",
              "एकदम सटीक!", "क्या बात!", "बहुत खूब!"]

WICKET_BODY = [
    "आउट!! {b} {w}, {r} रन बनाकर पवेलियन लौटे! {bl} की बड़ी सफलता!",
    "आउट है! {w} — {b} की पारी खत्म, {r} रन! {bl} ने किया कमाल!",
    "और मिल गया विकेट! {b} {w}, {r} रन पर खत्म! {bl} खुशी से झूमे!",
    "हट गया एक और! {b} {w} आउट, {r} रन बनाकर! {bl} की गेंद पर!",
    "बड़ा विकेट! {b} {w}, {r} रन! {bl} ने तोड़ी साझेदारी!",
    "अंपायर की उंगली उठी! {b} {w} आउट, {r} रन बनाकर गए!",
]
WICKET_AFTER = ["बड़ा मोड़!", "क्या झटका!", "गेम चेंजर!", "बड़ा विकेट!",
                "यहाँ से मुश्किल!", "बवाल मच गया!", "क्या मोड़ है!"]

DOT_CALL = [
    "कोई रन नहीं! {bl} की शानदार गेंद, {b} ने सम्हल कर खेला।",
    "डॉट बॉल! दबाव बढ़ रहा है, {bl} शानदार गेंदबाज़ी कर रहे हैं।",
    "रन नहीं मिला। {bl} बना रहे हैं दबाव!",
    "खाली गई! {bl} की गेंद पर {b} कुछ नहीं कर पाए।",
    "और एक और डॉट! {bl} ने रोके रन, स्कोर {sc}/{wk}।",
    "कुछ नहीं! {bl} की लाइन-लेंथ बिल्कुल सही, {b} सम्हले।",
    "{bl} का दबाव बरकरार, कोई रन नहीं!",
]

SINGLE_CALL = [
    "{b} ने सिंगल लिया और स्ट्राइक अपने पास रखी।",
    "एक रन! {b} ने हल्का सा धक्का दिया और भाग लिए।",
    "सिंगल मिल गया, {b} दूसरे छोर पर।",
    "और एक रन! {b} ने आसानी से ले लिया।",
]
DOUBLE_CALL = [
    "दो रन! {b} ने गेंद गैप में डाली और दो पूरे किए।",
    "और दो! शानदार रनिंग, {b} ने जल्दी दो रन ले लिए।",
    "दो रन! गेंद गैप में, और दोनों भाग पड़े।",
    "लीजिए दो रन! {b} की तेज़ रनिंग।",
]
TRIPLE_CALL = [
    "तीन रन! शानदार रनिंग, {b} और साथी ने तीन पूरे किए!",
    "और तीन! बढ़िया फील्डिंग के बावजूद तीन रन मिल गए।",
    "तीन रन! {b} ने भागकर तीन पूरे कर लिए।",
]
FIVE_CALL = [
    "पाँच रन! ओवरथ्रो से मिले अतिरिक्त रन।",
    "और पाँच! फील्डर की गलती, {b} को मिले पाँच रन!",
]


def _surname(name: str) -> str:
    """'VIRAT KOHLI' -> 'KOHLI'"""
    parts = (name or "").split()
    return parts[-1].title() if parts else ""


def expand_initials(name: str):
    """"V KOHLI" jaisa naam ho to poora naam dhoondho ("VIRAT KOHLI" -> विराट कोहली)."""
    parts = (name or "").split()
    if len(parts) != 2 or len(parts[0]) != 1:
        return None
    ini = parts[0][0].upper()
    sur = parts[1].lower()
    for full, hi in NAMES_HI.items():
        fp = full.split()
        if len(fp) >= 2 and fp[0][0].upper() == ini and fp[-1].lower() == sur:
            return hi
    return None


def hi_name(name: str) -> str:
    """Devanagari me naam; curated nahi mila to Latin surname (TTS theek padhta hai).

    Galat Hindi bolna, Latin naam se bhi bura hai — isliye 'andaza' kabhi nahi
    lagate. Jo dictionary me hai wahi bolein, warna asli (Latin) naam hi rahe.
    """
    key = (name or "").lower().strip()
    if not key:
        return "बल्लेबाज़"
    if key in NAMES_HI:
        return NAMES_HI[key]
    full = expand_initials(key)
    if full:
        return full
    last = key.split()[-1]
    if last in SURNAME_HI:
        return SURNAME_HI[last]
    return _surname(name) or "बल्लेबाज़"


def _dedup_lead(text: str) -> str:
    """pre aur template me ek hi shabd ho to woh do baar na bole."""
    for w in ("और", "देखिए", "लीजिए", "अरे!", "हाँ!", "ओहो!", "सुनिए!", "तो"):
        dbl = w + " " + w
        while dbl in text:
            text = text.replace(dbl, w, 1)
    return text


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
    pre = _pick(LEAD)
    F = dict(b=batter, bl=bowler, s=shot, sc=score, wk=wkts, r=ctx.get("striker_runs", 0))

    # ---- WICKET -----------------------------------------------------------
    if cls.get("is_wicket"):
        wtype = WICKET_HI.get(cls.get("wicket_type"), "आउट")
        F["w"] = wtype
        body = _pick(WICKET_BODY).format(**F)
        return _dedup_lead(f"{pre}{body} {_pick(WICKET_AFTER)} स्कोर {score}/{wkts}।")

    # ---- EXTRAS -----------------------------------------------------------
    et = cls.get("extra_type")
    if et == "WIDE":
        return (f"{pre}वाइड! अंपायर ने बाहों को फैलाया, टीम को मिला एक अतिरिक्त रन। "
                f"स्कोर {score}/{wkts}।")
    if et == "NO_BALL":
        return (f"{pre}नो बॉल! {bowler} का पैर आगे निकला — एक अतिरिक्त रन और आगे फ्री हिट!")
    if et in ("BYE", "LEG_BYE"):
        return f"{pre}गेंद बल्ले को नहीं लगी, लेकिन एक रन मिल गया। स्कोर {score}/{wkts}।"

    # ---- SIX --------------------------------------------------------------
    if cls.get("is_six"):
        tail = ""
        if target:
            tail = f" अब {need} रन और चाहिए, {balls_left} गेंद बाकी।"
        return _dedup_lead(f"{pre}" + _pick(SIX_CALL).format(**F) + f" {_pick(SIX_AFTER)}{tail}")

    # ---- FOUR -------------------------------------------------------------
    if cls.get("is_four"):
        tail = ""
        if target:
            tail = f" स्कोर {score}/{wkts}, {need} रन {balls_left} गेंद में चाहिए।"
        return _dedup_lead(f"{pre}" + _pick(FOUR_CALL).format(**F) + f" {_pick(FOUR_AFTER)}{tail}")

    # ---- runs -------------------------------------------------------------
    ev = cls.get("event")
    if ev == "RUN_TRIPLE":
        return _dedup_lead(f"{pre}" + _pick(TRIPLE_CALL).format(**F) + f" स्कोर {score}/{wkts}।")
    if ev == "RUN_DOUBLE":
        return _dedup_lead(f"{pre}" + _pick(DOUBLE_CALL).format(**F) + f" स्कोर {score}/{wkts}।")
    if ev == "RUN_SINGLE":
        return _dedup_lead(f"{pre}" + _pick(SINGLE_CALL).format(**F))
    if ev == "RUN_FIVE":
        return _dedup_lead(f"{pre}" + _pick(FIVE_CALL).format(**F) + f" स्कोर {score}/{wkts}।")

    # ---- dot --------------------------------------------------------------
    return _dedup_lead(f"{pre}" + _pick(DOT_CALL).format(**F))


def milestone_line(ms: str, ctx: dict) -> str:
    """MILESTONE ke liye lambi, joshili line (10 second ke card ke sath chalti hai)."""
    who = hi_name(ctx.get("striker_name", ""))
    bw = hi_name(ctx.get("bowler_name", ""))
    runs = ctx.get("striker_runs", 0)
    balls = ctx.get("striker_balls", 0)

    if ms == "FIFTY":
        return _pick([
            f"अर्धशतक पूरा! {who} की पचास — हेलमेट उठा, बल्ला हवा में, "
            f"और स्टेडियम तालियों से गूँज उठा! शाबाश!",
            f"और आ गयी पचासी! {who} ने {balls} गेंदों में जड़ा अपना फिफ्टी! "
            f"देखिए पूरी टीम खड़ी हो गयी, क्या पारी है!",
            f"पचास रन पूरे! {who} का अर्धशतक — बल्ले से आग बरस रही है! बवाल!",
        ])
    if ms == "HUNDRED":
        return _pick([
            f"शतक!! {who} ने जड़ दिया सौ! हेलमेट उठा, बल्ला हवा में, "
            f"और पूरा स्टेडियम खड़ा हो गया! क्या पारी है!",
            f"सौ रन पूरे!! {who} — {balls} गेंदों में शतक! बवाल मच गया, "
            f"दर्शकों का शोर आसमान छू रहा है!",
            f"और आ गया शतक! {who} ने अपना सौ पूरा किया, देखिए ड्रेसिंग रूम "
            f"भी खुशी से उछल पड़ा! कमाल!",
        ])
    if ms == "HAT_TRICK":
        return "हैट-ट्रिक!! तीन गेंदों में तीन विकेट — इतिहास रच दिया! बवाल मच गया!"

    # 150 / 200 / 250 / 300
    if ms.isdigit():
        n = int(ms)
        if n >= 150:
            return _pick([
                f"और ये तो कमाल है! {who} ने {n} रन पूरे कर लिए! "
                f"बल्ले से आग बरस रही है, देखिए क्या पारी है!",
                f"{n} रन!! {who} का बल्ला बोल रहा है, गेंदबाज़ बेबस! बवाल!",
            ])

    # bowling milestones — "5 WICKET HAUL" / "5W" dono chalne chahiye
    _w = None
    if isinstance(ms, str):
        import re as _re
        m = _re.match(r"^\s*(\d+)\s*W?(ICKET)?\s*(HAUL)?\s*$", ms, _re.I)
        if m:
            _w = m.group(1)
    if _w:
        w = _w
        return _pick([
            f"{w} विकेट! {bw} ने ये कारनामा कर दिखाया — कमाल की गेंदबाज़ी! "
            f"पूरा स्टेडियम तालियाँ बजा रहा है!",
            f"और {w} विकेट! {bw} की घातक गेंदबाज़ी — बल्लेबाज़ बेबस! बवाल!",
            f"{w} विकेट पूरे! {bw} ने तोड़ दी कमर, देखिए फील्डर्स भी दौड़ रहे हैं!",
            f"सुनिए! {w} विकेट हो गए {bw} के — क्या गेंदबाज़ी है, एकतरफ़ा मुक़ाबला!",
        ])

    # batting ka koi aur number (200 / 250 / 300)
    if isinstance(ms, str) and ms.isdigit() and int(ms) >= 50:
        return _pick([
            f"और ये तो कमाल है! {who} ने {ms} रन पूरे कर लिए! बवाल!",
            f"{ms} रन!! {who} का बल्ला बोल रहा है, गेंदबाज़ बेबस!",
        ])
    return _pick([
        f"और आ गया माइलस्टोन! {who} — {runs} रन! बवाल!",
        f"{who} की शानदार पारी, स्टेडियम गूँज उठा!",
    ])


def over_summary_line(over_no: int, runs: int, timeline: list, ctx: dict) -> str:
    """~10 second ka Hindi over-breakdown, har over ke aakhir me bajta hai."""
    bowler = hi_name(ctx.get("bowler_name", ""))
    b1 = hi_name(ctx.get("striker_name", ""))
    b2 = hi_name(ctx.get("non_striker_name", ""))
    seq = " ".join(str(t) for t in timeline) if timeline else "—"
    fours = sum(1 for t in timeline if str(t) == "4")
    sixes = sum(1 for t in timeline if str(t) == "6")
    wkts = sum(1 for t in timeline if str(t).upper() == "W")

    flavor = ("धमाकेदार ओवर" if runs >= 15 else
              "बढ़िया ओवर" if runs >= 9 else
              "किफ़ायती ओवर" if runs <= 4 else
              "संतुलित ओवर")
    bits = []
    if fours:
        bits.append(f"{fours} चौका" if fours == 1 else f"{fours} चौके")
    if sixes:
        bits.append(f"{sixes} छक्का" if sixes == 1 else f"{sixes} छक्के")
    if wkts:
        bits.append(f"{wkts} विकेट" if wkts == 1 else f"{wkts} विकेट")
    btxt = (" इस ओवर में " + " और ".join(bits) + " देखने को मिले!") if bits else ""

    score = ctx.get("score", 0)
    wk_total = ctx.get("wickets", 0)
    target = ctx.get("target", 0)
    need = ctx.get("need", 0)
    balls_left = ctx.get("balls_left", 0)
    crr = ctx.get("crr", 0.0)
    rrr = ctx.get("rrr", 0.0)
    chase = (f" टारगेट से अब {need} रन दूर, {balls_left} गेंद बाकी — "
             f"चाहिए {rrr} की रफ़्तार, चल रहा है {crr}।") if target else ""

    openers = [
        f"ओवर {over_no} का हाल सुनिए — {runs} रन, {flavor}!",
        f"तो ओवर {over_no} खत्म, {runs} रन निकले, {flavor}!",
        f"ओवर {over_no} पूरा — {runs} रन, {flavor}!",
    ]
    return (
        f"{_pick(openers)} "
        f"हर गेंद पर क्या हुआ: {seq}।{btxt} "
        f"{bowler} की गेंदबाज़ी में स्कोर अब {score}/{wk_total}। "
        f"क्रीज़ पर {b1} और {b2}।{chase}"
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
