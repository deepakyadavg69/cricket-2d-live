"""
probe.py — "is it still working?" self-test.

Run this FIRST after any deployment, and any time the stream goes quiet:

    python probe.py                 # auto-picked match
    python probe.py --match 174330  # specific match

It checks, in order:
    1. Cricbuzz reachable + RSC payload present
    2. miniscore parse (score / batsmen / bowler / rates)
    3. commentary text available for the classifier
    4. Hindi commentary generation
    5. edge-tts producing a real MP3
    6. local asset folders

Exit code 0 = healthy, 1 = something broke (it tells you what).
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import sys

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

OK, BAD, WARN = "  [ OK ]", "  [FAIL]", "  [WARN]"
results = []


def check(ok: bool, label: str, detail: str = "", warn_only: bool = False):
    tag = OK if ok else (WARN if warn_only else BAD)
    print("%s %-42s %s" % (tag, label, detail))
    results.append(bool(ok) or warn_only)
    return ok


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default="auto")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    args = ap.parse_args()

    print("\n" + "=" * 74)
    print("  LIVE CRICKET 2D ENGINE — system probe")
    print("=" * 74)

    # ---- 1. imports -------------------------------------------------------
    print("\n[1] Dependencies")
    try:
        import aiohttp  # noqa
        check(True, "aiohttp")
    except Exception as exc:
        check(False, "aiohttp", str(exc))
    try:
        import yaml  # noqa
        check(True, "PyYAML")
    except Exception as exc:
        check(False, "PyYAML", str(exc))
    try:
        import edge_tts  # noqa
        check(True, "edge-tts (Hindi voice)")
    except Exception as exc:
        check(False, "edge-tts (Hindi voice)", "pip install edge-tts", warn_only=True)

    from scraper import CricbuzzScraper, CRICBUZZ
    from classifier import classify
    from commentary import CommentaryEngine
    from audio_engine import AudioEngine

    # ---- 2. network + parse ------------------------------------------------
    print("\n[2] Cricbuzz ingestion")
    s = await CricbuzzScraper(match_id=args.match).start()
    matches = await s.list_matches()
    check(len(matches) > 0, "live-scores board reachable", "%d matches listed" % len(matches))
    if not matches:
        print("\nCricbuzz is unreachable or blocked from this IP. Try again from a")
        print("different network, or check https://www.cricbuzz.com in a browser.")
        return 1

    resolved = await s.resolve_match()
    check(bool(resolved), "match resolved", str(s.match_id))
    if not resolved:
        return 1

    snap = await s.snapshot()
    check(bool(snap), "miniscore parsed", s.status)
    if not snap:
        print("\nThe RSC payload no longer contains a 'miniscore' object.")
        print("Cricbuzz likely shipped a redesign — the parser needs updating.")
        return 1

    print("\n[3] Match data")
    print("      title   : %s" % snap.get("title"))
    print("      series  : %s (%s)" % (snap.get("series"), snap.get("format")))
    print("      venue   : %s" % (snap.get("venue") or "-"))
    print("      state   : %s   live=%s" % (snap.get("match_state"), snap.get("is_live")))
    print("      score   : %s/%s in %s   (innings %s)"
          % (snap["score"], snap["wickets"], snap["overs"], snap["innings_id"]))
    print("      striker : %s %s (%s)   bowler: %s %s-%s-%s-%s"
          % (snap["striker"]["name"], snap["striker"]["runs"], snap["striker"]["balls"],
             snap["bowler"]["name"], snap["bowler"]["overs"], snap["bowler"]["maidens"],
             snap["bowler"]["runs"], snap["bowler"]["wickets"]))
    print("      target  : %s   CRR %s   RRR %s" % (snap["target"], snap["crr"], snap["rrr"]))
    print("      this over: %s" % (snap["this_over"] or []))

    check(bool(snap["striker"]["name"]), "striker name present")
    check(bool(snap["bowler"]["name"]), "bowler name present")
    check(bool(snap.get("title")), "team names / title")
    check(bool(snap["this_over"]) or bool(snap["recent_ovs"]), "ball timeline available")

    # ---- 4. classifier + commentary ---------------------------------------
    print("\n[4] Classifier + Hindi commentary")
    eng = CommentaryEngine({})
    ctx = {
        "runs": 6, "score": snap["score"], "wickets": snap["wickets"],
        "target": snap["target"], "need": snap["need"], "balls_left": snap["balls_left"],
        "striker_name": snap["striker"]["name"],
        "non_striker_name": snap["non_striker"]["name"],
        "bowler_name": snap["bowler"]["name"],
        "striker_runs": snap["striker"]["runs"], "striker_balls": snap["striker"]["balls"],
    }
    sample = "Lofted over long-on, that's a massive SIX!"
    cls = classify(sample, 6, False, False)
    check(cls["shot"] == "SHOT_SIX", "shot classification", cls["shot"])
    line = await eng.ball_line(cls, ctx, sample)
    check(bool(line), "Hindi line generated", line[:60])
    print("      %s" % line)

    over_line = await eng.over_line(3, 12, ["1", "4", "0", "6", "W", "2"], ctx)
    check(len(over_line) > 40, "over-summary line generated", "%d chars" % len(over_line))

    # ---- 5. TTS ------------------------------------------------------------
    print("\n[5] Hindi text-to-speech (edge-tts)")
    au = AudioEngine({})
    if not au.enabled:
        check(False, "edge-tts available", "pip install edge-tts", warn_only=True)
    else:
        url = await au.synth(line)
        if url:
            name = url.rsplit("/", 1)[-1]
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "tts", name)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            check(size > 2000, "MP3 synthesised", "%s (%d bytes)" % (name, size))
        else:
            check(False, "MP3 synthesised", "edge-tts returned nothing — offline or rate-limited")

    # ---- 6. assets ---------------------------------------------------------
    print("\n[6] Optional assets")
    a = au.assets()
    check(len(a["bgm"]) > 0, "background music file", "%d found" % len(a["bgm"]), warn_only=True)
    check(len(a["sfx"]) > 0, "sound effects", ", ".join(sorted(a["sfx"])) or "none (synth fallback)", warn_only=True)
    if not a["bgm"]:
        print("        -> drop a loop in assets/bgm/  (see assets/bgm/README.md)")
    if not a["sfx"]:
        print("        -> drop six.mp3 four.mp3 wicket.mp3 in assets/sfx/")

    await s.close()

    healthy = all(results)
    print("\n" + "=" * 74)
    print("  RESULT: %s" % ("HEALTHY — ready to stream" if healthy else "PROBLEMS FOUND (see FAIL lines above)"))
    print("  Match URL: %s/live-cricket-scores/%s/live" % (CRICBUZZ, s.match_id))
    print("=" * 74 + "\n")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
