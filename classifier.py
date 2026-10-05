"""
classifier.py — turn English commentary text into visual / animation triggers.

Keyword tables are ordered by specificity: the first rule that matches wins, so
"cover drive" must be tested before the generic "drive", and "run out" before
"out". Everything is lowercase-matched against the raw commentary string.

Outputs are the exact event ids the HTML overlay understands:

    shots   SHOT_PULL SHOT_HOOK SHOT_DRIVE SHOT_STRAIGHT SHOT_CUT SHOT_SIX
            SHOT_FLICK SHOT_SWEEP SHOT_DEFENSE SHOT_LEAVE
    runs    RUN_SIX RUN_FOUR RUN_TRIPLE RUN_DOUBLE RUN_SINGLE DOT_BALL
    outs    OUT_BOWLED OUT_CAUGHT OUT_RUNOUT OUT_LBW OUT_STUMPED OUT_HITWICKET
    misc    WIDE NO_BALL BYE LEG_BYE EVENT_OVER_END EVENT_INNINGS_BREAK
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
#  Rule tables (pattern order matters — most specific first)
# ---------------------------------------------------------------------------
SHOT_RULES = [
    ("SHOT_SIX",      ["sixer", "six runs", "maximum", "into the stands", "over long-on",
                       "over long-off", "over mid-wicket", "over the ropes", "out of the ground",
                       "lofted", "slogsweep", "goes all the way", "sails over", "clears the"]),
    ("SHOT_PULL",     ["pull shot", "pulls", "pulled", "short arm pull", "muscles it"]),
    ("SHOT_HOOK",     ["hooks", "hooked", "hook shot", "round the corner"]),
    ("SHOT_SWEEP",    ["sweeps", "swept", "reverse sweep", "paddle sweep", "slog sweep"]),
    ("SHOT_CUT",      ["late cut", "square cut", "upper cut", "cuts it", "cut shot",
                       "cutting", "backward point", "guided to third man"]),
    ("SHOT_DRIVE",    ["cover drive", "extra cover drive", "driven down", "drives", "driven",
                       "punched off the back foot", "through covers", "through the covers",
                       "into the covers", "past cover"]),
    ("SHOT_STRAIGHT", ["straight drive", "down the ground", "straight down", "past the bowler",
                       "long-on", "long-off", "straight past"]),
    ("SHOT_FLICK",    ["flick", "clipped off", "off the pads", "whipped", "glanced",
                       "tickled", "fine leg", "off his hip"]),
    ("SHOT_DEFENSE",  ["defended", "forward defence", "forward defense", "blocked",
                       "dead bat", "pushed to", "dabs", "dabbed", "shouldered arms",
                       "leaves it", "left alone", "no shot offered"]),
]

WICKET_RULES = [
    ("OUT_BOWLED",    ["bowled", "stumps shattered", "timber", "castled", "knocked over",
                       "ball hits the stumps", "off stump is pegged"]),
    ("OUT_RUNOUT",    ["run out", "direct hit", "short of the crease", "throws it at the stumps"]),
    ("OUT_STUMPED",   ["stumped", "stumping"]),
    ("OUT_HITWICKET", ["hit wicket", "hitwicket"]),
    ("OUT_LBW",       ["lbw", "leg before", "leg-before", "plumb", "trapped in front"]),
    ("OUT_CAUGHT",    ["caught", "caught by", "taken at", "caught and bowled", "c & b",
                       "in the air", "holes out", "finds the fielder", "caught at",
                       "skier", "taken by"]),
]

# NOTE: keep these specific. "off the pad(s)" is NOT a leg-bye — it is used in
# ordinary flick/glance commentary and caused false positives.
EXTRA_RULES = [
    ("WIDE",    ["wide", "wides", "called a wide", "down the leg side called wide"]),
    ("NO_BALL", ["no ball", "noball", "no-ball", "free hit"]),
    ("LEG_BYE", ["leg bye", "leg byes", "legbye", "lb "]),
    ("BYE",     [" bye", "single bye", "one bye", "bye!", "byes taken"]),
]

MILESTONE_RULES = [
    ("FIFTY",    ["brings up his fifty", "brings up her fifty", "fifty for", "reaches his fifty", "half-century", "half century", "50 off"]),
    ("HUNDRED",  ["century", "brings up his ton", "hundred for", "reaches his hundred", "100 off", "ton"]),
    ("HAT_TRICK",["hat-trick", "hat trick"]),
    ("FIVE_FOR", ["five-wicket haul", "five wicket haul", "fifer", "5 for"]),
]

# Hindi labels used by the commentary generator
SHOT_HI = {
    "SHOT_SIX": "ज़ोरदार शॉट",
    "SHOT_PULL": "पुल शॉट",
    "SHOT_HOOK": "हुक शॉट",
    "SHOT_SWEEP": "स्वीप शॉट",
    "SHOT_CUT": "कट शॉट",
    "SHOT_DRIVE": "ड्राइव",
    "SHOT_STRAIGHT": "स्ट्रेट ड्राइव",
    "SHOT_FLICK": "फ्लिक शॉट",
    "SHOT_DEFENSE": "डिफेंसिव शॉट",
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


def _match(rules, text: str):
    for name, kws in rules:
        for kw in kws:
            if kw in text:
                return name
    return None


def clean_text(t: str) -> str:
    """Strip Cricbuzz format placeholders like 'B0$' / 'I0$' and extra spaces."""
    if not t:
        return ""
    t = re.sub(r"\b[A-Z]\d+\$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def classify_shot(text: str, runs: int = 0, wicket: bool = False) -> str:
    t = clean_text(text).lower()
    if not t:
        # No commentary text for this delivery (happens on some feeds) —
        # pick a sensible animation from the runs alone.
        if runs >= 6:
            return "SHOT_SIX"
        if runs >= 1:
            return "SHOT_DRIVE"
        return "SHOT_DEFENSE"
    hit = _match(SHOT_RULES, t)
    if hit:
        return hit
    if runs >= 6:
        return "SHOT_SIX"
    if runs in (4, 5):
        return "SHOT_DRIVE"     # no text to go on -> a safe, good-looking drive
    if runs in (1, 2, 3):
        return "SHOT_DRIVE"
    return "SHOT_DEFENSE"


def classify_wicket(text: str) -> str:
    t = clean_text(text).lower()
    # "caught" is very common as a substring; bowled/lbw must win when present
    hit = _match(WICKET_RULES, t)
    return hit or "OUT_CAUGHT"


def classify_extra(text: str) -> Optional[str]:
    t = clean_text(text).lower()
    return _match(EXTRA_RULES, t)


def classify_milestone(text: str) -> Optional[str]:
    t = clean_text(text).lower()
    return _match(MILESTONE_RULES, t)


def run_event(runs: int, extra: bool = False) -> str:
    if runs >= 6:
        return "RUN_SIX"
    if runs == 5:
        return "RUN_FIVE"
    if runs == 4:
        return "RUN_FOUR"
    if runs == 3:
        return "RUN_TRIPLE"
    if runs == 2:
        return "RUN_DOUBLE"
    if runs == 1:
        return "RUN_SINGLE"
    return "DOT_BALL"


def classify(text: str, runs: int = 0, wicket: bool = False, extra: bool = False) -> dict:
    """
    One call -> everything the renderer and audio engine need.

    Returns
    -------
    {
      shot, event, wicket_type, extra_type, milestone,
      is_boundary, is_six, is_four, is_wicket, is_dot,
      shot_hi, wicket_hi
    }
    """
    t = clean_text(text)
    if wicket:
        wtype = classify_wicket(t)
        shot = classify_shot(t, runs, True)
        return {
            "shot": shot,
            "event": "WICKET",
            "wicket_type": wtype,
            "extra_type": None,
            "milestone": None,
            "is_boundary": False,
            "is_six": False,
            "is_four": False,
            "is_wicket": True,
            "is_dot": True,
            "shot_hi": SHOT_HI.get(shot, "शॉट"),
            "wicket_hi": WICKET_HI.get(wtype, "आउट"),
        }

    etype = classify_extra(t)
    ev = run_event(runs, extra or bool(etype))
    shot = classify_shot(t, runs, False)
    ms = classify_milestone(t)

    return {
        "shot": shot,
        "event": ev,
        "wicket_type": None,
        "extra_type": etype,
        "milestone": ms,
        "is_boundary": ev in ("RUN_FOUR", "RUN_SIX"),
        "is_six": ev == "RUN_SIX",
        "is_four": ev == "RUN_FOUR",
        "is_wicket": False,
        "is_dot": ev == "DOT_BALL",
        "shot_hi": SHOT_HI.get(shot, "शॉट"),
        "wicket_hi": None,
    }


# ---------------------------------------------------------------------------
#  Self test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    samples = [
        ("Kohli goes down the ground, lofted over long-on, SIX!", 6, False, False),
        ("Short ball, pulled away to deep square leg for FOUR", 4, False, False),
        ("Back of a length, defended off the back foot", 0, False, False),
        ("Full and wide, driven sweetly through covers for four", 4, False, False),
        ("OUT! Bowled! Stumps shattered, the off stump is pegged back", 0, True, False),
        ("Caught at deep mid-wicket, holes out to the fielder", 0, True, False),
        ("Flicked off the pads, they take a quick single", 1, False, False),
        ("Down the leg side, called a wide", 1, False, True),
        ("Direct hit at the striker's end, RUN OUT!", 0, True, False),
        ("Brings up his fifty with a beautiful cover drive", 4, False, False),
    ]
    for txt, r, w, e in samples:
        c = classify(txt, r, w, e)
        print("%-62s -> %-11s %-13s %s" % (txt[:62], c["event"], c["shot"], c["wicket_type"] or ""))
