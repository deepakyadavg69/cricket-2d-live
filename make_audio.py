#!/usr/bin/env python3
"""
make_audio.py — stadium BGM + commentary SFX khud banata hai.

KYUN: copyright se bachne ka sabse pakka tareeka. Ye sab sounds
numpy/scipy se synthesize ki gayi hain — koi sample, koi gaana, koi
recording istemal nahi hui. Isliye ye 100% royalty-free hain aur
YouTube Content ID inhe kabhi flag nahi karega.

Chalane ka tareeka:
    pip install numpy scipy lameenc
    python3 make_audio.py

Banane wali files:
    assets/bgm/stadium_day.mp3   ~24 sec ka seamless loop (crowd murmur)
    assets/sfx/bat.mp3           ball ka bat se takrana
    assets/sfx/four.mp3          chauke par bheed ki cheer
    assets/sfx/six.mp3           chhakke par zor ka roar + whistle
    assets/sfx/wicket.mp3        wicket: bail girne ki thap + appeal + roar
    assets/sfx/cheer.mp3         milestone par taliyon ki bhed
    assets/sfx/whistle.mp3       umpire ki seeti

Agar chahein to apna mp3 bhi inhi naam se assets/ me daal sakte hain —
server wahi use karega (yahan banayi files overwrite ho jayengi).
"""

import os
import numpy as np
from scipy.signal import butter, sosfilt
import lameenc

SR = 44100
ROOT = os.path.dirname(os.path.abspath(__file__))
BGM_DIR = os.path.join(ROOT, "assets", "bgm")
SFX_DIR = os.path.join(ROOT, "assets", "sfx")


# --------------------------------------------------------------- helpers ---
def bp(sig, lo, hi, order=4):
    """Band-pass filter (Hz)."""
    sos = butter(order, [lo / (SR / 2), hi / (SR / 2)], btype="band", output="sos")
    return sosfilt(sos, sig)


def lp(sig, hz, order=4):
    sos = butter(order, hz / (SR / 2), btype="low", output="sos")
    return sosfilt(sos, sig)


def hp(sig, hz, order=4):
    sos = butter(order, hz / (SR / 2), btype="high", output="sos")
    return sosfilt(sos, sig)


def noise(n):
    return np.random.default_rng().standard_normal(n)


def pink(n):
    """Pink noise — Voss-McCartney ka simple approximation."""
    w = np.random.default_rng().standard_normal(n)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1, -2.494956002, 2.017265875, -0.522189400]
    from scipy.signal import lfilter
    return lfilter(b, a, w)


def env(n, attack, decay, power=1.0):
    """Attack/decay envelope, 0 se 1 tak."""
    a = max(1, int(attack * SR))
    d = max(1, int(decay * SR))
    e = np.ones(n)
    if a < n:
        e[:a] = np.linspace(0, 1, a)
    tail = np.exp(-np.linspace(0, 6.0, min(d, n))) ** power
    e[-len(tail):] *= tail
    return e


def norm(sig, head=0.89):
    m = np.max(np.abs(sig))
    return sig * (head / m) if m > 0 else sig


def fade(sig, ms=15):
    n = max(1, int(SR * ms / 1000))
    sig = sig.copy()
    sig[:n] *= np.linspace(0, 1, n)
    sig[-n:] *= np.linspace(1, 0, n)
    return sig


def write_mp3(path, sig, bitrate=96):
    sig = norm(sig)
    pcm = np.clip(sig, -1, 1)
    pcm = (pcm * 32767).astype(np.int16)
    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate)
    enc.set_in_sample_rate(SR)
    enc.set_channels(1)
    enc.set_quality(2)
    data = enc.encode(pcm.tobytes())
    data += enc.flush()
    with open(path, "wb") as f:
        f.write(bytes(data))
    print("  %-34s %6.1f KB" % (os.path.relpath(path, ROOT), len(data) / 1024))


# ------------------------------------------------------------ BGM: crowd ---
def crowd_bed(n):
    """Door ki bheed ki gun-gun (murmur) — halka, kabhi thakane wala nahi."""
    base = pink(n)
    base = lp(base, 700)
    # dheemi lehrein — bheed kabhi tez kabhi dheemi
    t = np.arange(n) / SR
    slow = 0.72 + 0.28 * np.sin(2 * np.pi * 0.06 * t) * np.sin(2 * np.pi * 0.021 * t + 1.1)
    base *= slow
    # thodi si "baat-cheet" — madhyam band
    chat = bp(noise(n), 300, 1700)
    chat *= (0.5 + 0.5 * np.sin(2 * np.pi * 0.13 * t + 0.4))
    return 0.85 * base + 0.30 * chat


def distant_roar(dur, level=0.35, seed=0):
    """Door se aati hui ek chhoti cheer (BGM me kabhi-kabhi)."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    r = bp(rng.standard_normal(n), 350, 1800, order=3)
    # pehle uthe, phir dhire se utre
    e = np.exp(-((t - dur * 0.32) ** 2) / (2 * (dur * 0.26) ** 2))
    return level * r * e


def make_bgm(seconds=24.0, fade_s=2.0):
    n = int(seconds * SR)
    sig = crowd_bed(n + int(fade_s * SR))

    # kabhi-kabhi door se cheer (har ~6 second me, halki)
    for i, at in enumerate(np.arange(3.5, seconds + fade_s, 6.2)):
        s = int(at * SR)
        seg = distant_roar(1.5, level=0.30, seed=100 + i)
        seg = lp(seg, 1200)                       # door ki awaaz = bhari
        end = min(len(sig), s + len(seg))
        sig[s:end] += seg[: end - s]

    # seamless loop: aakhir ka hissa shuruat me ghula do
    f = int(fade_s * SR)
    n_out = int(seconds * SR)
    head, tail = sig[:f].copy(), sig[n_out: n_out + f]
    w = np.linspace(0, 1, f)
    sig = sig[:n_out]
    sig[:f] = head * (1 - w) + tail * w
    return fade(sig, 40)


# ------------------------------------------------------------------ SFX ----
def sfx_bat():
    """Ball ka bat se takrana — 'thak' + halki 'click'."""
    n = int(0.16 * SR)
    t = np.arange(n) / SR
    click = noise(n) * np.exp(-t / 0.0022)
    click = hp(click, 1800)
    body = (np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.030)
            + 0.6 * np.sin(2 * np.pi * 415 * t) * np.exp(-t / 0.018))
    crack = bp(noise(n), 2200, 7000) * np.exp(-t / 0.010)
    return norm(0.75 * click + 1.0 * body + 0.55 * crack)


def roar(dur, lo, hi, attack, level=1.0, seed=0, whoosh=True):
    """Bheed ka shor — chauke/chhakke/milestone sab isi se bante hain."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    r = bp(rng.standard_normal(n), lo, hi, order=3)
    e = env(n, attack, dur * 0.75, power=0.85)
    # shuru me thoda tez, phir dheere
    e *= (0.85 + 0.35 * np.exp(-t / (dur * 0.45)))
    out = r * e
    if whoosh:
        # hawa jaisa sweep jo bheed ke upar baith jaye
        f = np.linspace(180, 900, n)
        ph = 2 * np.pi * np.cumsum(f) / SR
        out += 0.16 * np.sin(ph) * e
    return level * out


def sfx_four():
    return 0.92 * roar(1.7, 420, 2100, 0.07, seed=11)


def sfx_six():
    r = 1.0 * roar(2.6, 380, 3000, 0.06, seed=22)
    # upar se ek tez seeti (bheed khushi me seeti bajati hai)
    n = int(2.6 * SR)
    t = np.arange(n) / SR
    w = np.sin(2 * np.pi * 2350 * t + 0.6 * np.sin(2 * np.pi * 5.5 * t))
    w *= env(n, 0.25, 1.4) * np.clip((t - 0.35) / 0.2, 0, 1)
    return norm(r + 0.10 * w)


def sfx_wicket():
    n = int(2.9 * SR)
    t = np.arange(n) / SR
    # 1) bail gire — lakdi ki thap
    clack = noise(int(0.09 * SR)) * np.exp(-np.arange(int(0.09 * SR)) / SR / 0.006)
    clack = bp(clack, 900, 4200)
    out = np.zeros(n)
    out[: len(clack)] += 0.9 * clack
    # 2) appeal — "hauuuuz" (gehra, utarta hua)
    ap = bp(noise(n), 260, 950, order=3)
    ap *= np.exp(-((t - 0.22) ** 2) / (2 * 0.20 ** 2))
    out += 0.55 * ap
    # 3) phir poori bheed ka shor
    out += 0.95 * roar(n / SR, 330, 2000, 0.30, seed=33)
    return norm(out)


def sfx_cheer():
    """Taliyon ki bhed (milestone par)."""
    n = int(2.3 * SR)
    t = np.arange(n) / SR
    # taliyan = chhote chhote random noise bursts
    claps = np.zeros(n)
    rng = np.random.default_rng(7)
    tt = 0.0
    while tt < 2.2:
        i = int(tt * SR)
        dur = int(0.012 * SR)
        burst = rng.standard_normal(dur) * np.exp(-np.arange(dur) / (SR * 0.004))
        claps[i:i + dur] += burst * (0.6 + 0.4 * rng.random())
        tt += 0.085 + rng.random() * 0.075      # ~12 thappad/second, thoda jitter
    claps = bp(claps, 1200, 5200, order=2)
    # aur upar se bheed ki khushi
    return norm(0.85 * claps + 0.7 * roar(2.3, 400, 2400, 0.15, seed=44))


def sfx_whistle():
    n = int(0.85 * SR)
    t = np.arange(n) / SR
    f = 2080 + 55 * np.sin(2 * np.pi * 6.5 * t)
    ph = 2 * np.pi * np.cumsum(f) / SR
    w = np.sin(ph) + 0.30 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
    # seeti me hawa ka shor bhi
    w = w * 0.85 + 0.15 * bp(noise(n), 1800, 4200)
    w *= env(n, 0.05, 0.6, power=1.2)
    w *= np.clip((0.85 - t) / 0.12, 0, 1)      # aakhir me band
    return norm(fade(w, 8))


# ------------------------------------------------------------------ main ---
def main():
    os.makedirs(BGM_DIR, exist_ok=True)
    os.makedirs(SFX_DIR, exist_ok=True)
    print("BGM + SFX synthesize ho rahe hain (100% royalty-free, computer-generated)…\n")

    write_mp3(os.path.join(BGM_DIR, "stadium_day.mp3"), make_bgm(24.0), bitrate=96)

    jobs = [
        ("bat.mp3",     sfx_bat),
        ("four.mp3",    sfx_four),
        ("six.mp3",     sfx_six),
        ("wicket.mp3",  sfx_wicket),
        ("cheer.mp3",   sfx_cheer),
        ("whistle.mp3", sfx_whistle),
    ]
    for name, fn in jobs:
        write_mp3(os.path.join(SFX_DIR, name), fn(), bitrate=80)

    print("\nHo gaya. Ab server inhe automatically use karega.")
    print("Apni pasand ki file chahiye to bas usi naam se assets/ me rakh do.\n")


if __name__ == "__main__":
    main()
