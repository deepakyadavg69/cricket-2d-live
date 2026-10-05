# 🏏 Live Cricket 2D Engine — Automated YouTube Live Broadcast

**Tablet + mobile se chalane wala poora system.** Cricbuzz se live ball-by-ball
scrape hota hai → Hindi me AI commentary banti hai (edge-tts, free) → 2D ground
pe animation chalti hai → aapka tablet screen-capture karke YouTube pe stream
karta hai. Ek baar chalu kar diya to poora match **zero manual intervention**.

---

## 📲 Tablet/phone se deploy karna hai? → **`DEPLOY_RENDER.md` padho**

Agar aapke paas PC nahi hai to `deploy/` folder me **ek single self-extracting
`app.py`** banaya gaya hai — sirf 4 files GitHub pe upload karne hain aur Render
Blueprint baaki sab khud kar lega. Poora click-by-click guide
[`DEPLOY_RENDER.md`](DEPLOY_RENDER.md) me hai.

```bash
python build_deploy.py     # deploy/ folder banata hai (4 files)
```

---

## 🚀 5-minute quick start (Hinglish)

```bash
# 1. dependencies
pip install -r requirements.txt

# 2. health check — sab kuch theek hai?
python probe.py

# 3. chalao
python main.py

# 4. browser me kholo
http://localhost:8080
```

Phir tablet/phone se is URL ko Chrome me fullscreen kholo aur Prism Live Studio
se **ScreenCast** karke YouTube pe live karo. Poora step-by-step neeche hai.

---

## 🏗️ Architecture

```
   Cricbuzz.com (Next.js / RSC)
            │  har 5 second me HTML poll
            ▼
   ┌─────────────────┐
   │  scraper.py     │  RSC payload → miniscore + commentary
   └────────┬────────┘
            │ BallDetector: score/ball/wicket ka difference → ek delivery
            ▼
   ┌─────────────────┐
   │ classifier.py   │  "lofted over long-on" → SHOT_SIX / RUN_SIX
   └────────┬────────┘
            │
   ┌────────┴────────┐
   ▼                 ▼
┌──────────────┐  ┌──────────────────┐
│commentary.py │  │   main.py        │  aiohttp server
│EN → Hindi    │  │  REST + WebSocket│
└──────┬───────┘  └────────┬─────────┘
       │                   │  ws:// → {ball, tts, sfx, over_end}
       ▼                   ▼
┌──────────────┐  ┌──────────────────────────────┐
│audio_engine  │  │ overlay/index.html (1920x1080)│
│edge-tts→MP3  │─►│ canvas 2D · scorebar · ticker │
│/audio/tts/.. │  │ BGM + SFX + auto-ducking 🎧   │
└──────────────┘  └───────────────┬──────────────┘
                                  │  Prism Live Studio: ScreenCast + Internal Audio
                                  ▼
                            YouTube Live
```

**Important design decision:** server sirf **MP3 files banata hai**, awaz nahi
nikalta. Saara audio browser me bajta hai (`<audio>` + Web Audio API), jisse:

- server pe koi sound card / VB-CABLE / ffmpeg routing nahi chahiye
- Render/Koyeb ke free tier pe bhi chalta hai
- Prism Live ka **Internal Audio** capture ek hi baar me sab kuch pakad leta hai

---

## ☁️ Deploy: Render.com free tier (recommended, 10 min)

> **Tablet/phone se kar rahe ho?** Ye wala section chhodo, seedha
> [`DEPLOY_RENDER.md`](DEPLOY_RENDER.md) padho — wahan folder-flatten wali
> problem ka solution hai.

> Kyun cloud? Tablet me Termux chalana possible hai, par Android background me
> Python ko maar deta hai. Cloud pe backend 24×7 chalega aur tablet sirf
> display karega — battery bhi bachegi, heat bhi kam.

1. **GitHub repo banao** — is folder ko push kar do.
   ```bash
   git init && git add . && git commit -m "cricket 2d engine" && git push
   ```
   (`render.yaml` already hai, Render ise pehchan lega.)

2. **render.com** pe jao → **New → Blueprint** → apna repo select karo.
   Render `render.yaml` padh lega aur `pip install -r requirements.txt` +
   `python main.py` automatically set kar dega.

3. **Deploy** dabao. ~3 minute lagenge. Mil jayega:
   `https://cricket-2d-live.onrender.com`

4. **Test karo:**
   ```
   https://cricket-2d-live.onrender.com/api/health     →  "ok": true
   https://cricket-2d-live.onrender.com/api/matches    →  live match list
   https://cricket-2d-live.onrender.com/               →  overlay
   ```

5. **Free tier so-gaya problem:** Render free instance 15 minute inactive
   rehne pe sleep kar deta hai. Jab tak aapka tablet overlay khol kar poll kar
   raha hai (har 5s), ye jagta rahega. Lekin match se **10 minute pehle
   overlay khol lena** — warna pehli request ko cold-start ke ~40 second
   lagenge.

**Alternatives:** Koyeb (`koyeb.com`, same steps), Railway, ya Oracle Cloud
Always-Free VM (sabse stable, thoda lamba setup).

---

## 📱 Tablet setup: Prism Live Studio → YouTube

### Pehle ye check karo
- YouTube channel **verified** ho (youtube.com/verify, 24h lagte hain)
- **50+ subscribers** hon — mobile live streaming ke liye zaroori hai [4](https://gyre.pro/blog/types-of-live-streams-on-youtube)
- Android version 10+ (internal audio capture ke liye)

### Steps

1. **Chrome** (tablet) me kholo:
   ```
   https://cricket-2d-live.onrender.com/?auto=1
   ```
   `?auto=1` = audio-unlock wali "TAP TO GO LIVE" screen skip ho jayegi.
   **Fullscreen** kar do (⋮ → Full screen) aur screen rotation lock kar do.

   > Agar awaz nahi aa rahi: `?auto=1` hatakar kholo aur ek baar screen par
   > tap karo — Android Chrome bina gesture ke audio nahi chalata.

2. **Prism Live Studio** kholo → **Live** → platform me **YouTube** chuno.

3. Source chuno: **ScreenCast** (screen capture).
   ⚠️ Prism ka **"Web" (webpage) source mat use karna** — usme webpage ki
   awaz aksar capture nahi hoti [3](https://prismlive.com/en_us/faq/faq.html).
   ScreenCast + Chrome fullscreen sabse reliable hai.

4. **Audio settings** me:
   - **Internal sound / device audio volume** = 100% (yahi aapki Hindi
     commentary + BGM + SFX hai)
   - **Mic volume** = 0% (ya kam, agar khud bolna ho to)

5. **Resolution** = 1080p, **bitrate** = 8000–12000 kbps, orientation =
   landscape.

6. **Go Live.** Chrome ko foreground me rehne do — Prism background se capture
   karta rahega.

7. Tablet ko **charger pe rakho** aur Chrome ko background-kill hone se bachao
   (Settings → Apps → Chrome → Battery → Unrestricted).

> Agar Prism Live me internal audio ka option na dikhe to Android 10+ confirm
> karo, ya **Turnip** / **Streamlabs** try karo.

---

## 🎯 Match kaise chuno

```bash
# saare live matches dekho
curl https://<your-host>/api/matches
```

```json
{"matches": [
  {"match_id": "174330", "team1": "Sach", "team2": "Wich",
   "state": "In Progress", "format": "T20", "series": "World Championship of Legends 2026"}
]}
```

Phir ya to:

```bash
# runtime pe switch karo (restart nahi chahiye)
curl -X POST https://<your-host>/api/match -H 'Content-Type: application/json' \
     -d '{"match_id":"174330"}'
```

ya `config.yaml` me pin kar do:

```yaml
scraper:
  match_id: "174330"     # "auto" = pehla live match automatically
  series_filter: ""      # sirf "ipl" / "india" wale matches chahiye to
```

---

## 🎵 BGM aur SFX add karna

| Folder | Kya daalein |
|---|---|
| `assets/bgm/` | ek royalty-free loop, jaise `stadium.mp3` |
| `assets/sfx/` | `six.mp3`, `four.mp3`, `wicket.mp3` (optional: `cheer.mp3`, `whistle.mp3`, `bat.mp3`) |

Free sources: YouTube Studio → Audio Library, Pixabay Music/Sounds, Freesound (CC0).

**Agar kuch bhi upload na karo to bhi chalega** — browser crowd-noise aur
whistle/cheer Web Audio se khud bana leta hai. Rehearsal ke liye kaafi hai.

Cloud deploy me ye files git ke saath jayengi (`.gitignore` unhe ignore karta
hai, to deploy se pehle `.gitignore` se wo lines hata dena, ya Render ke
"Environment → Disk" me upload karna).

---

## 🎛️ Config reference (`config.yaml`)

| Key | Default | Matlab |
|---|---|---|
| `scraper.match_id` | `"auto"` | Cricbuzz match id, ya `"auto"` |
| `scraper.series_filter` | `""` | sirf is series ke matches |
| `scraper.poll_seconds` | `5` | 3 se kam mat karna (rate-limit) |
| `audio.voice` | `hi-IN-MadhurNeural` | `hi-IN-SwaraNeural` bhi try kar sakte ho |
| `audio.rate` | `+12%` | bolne ki speed |
| `audio.pitch` | `+6Hz` | excitement ke liye thoda upar |
| `commentary.enabled` | `false` | `true` = LLM se aur natural Hindi |
| `commentary.provider` | `gemini` | `gemini` ya `openai` |
| `server.port` | `8080` | `$PORT` env hamesha jeet ta hai |

**LLM commentary on karna hai?** `commentary.enabled: true` karo aur Render ke
Environment me `GEMINI_API_KEY` daalo. Key nahi mili ya API slow hui to
automatic template wali Hindi use ho jayegi — stream kabhi chup nahi hoga.

---

## 🩺 Troubleshooting

```bash
python probe.py --match <id>     # sabse pehle ye chalao
```

| Symptom | Fix |
|---|---|
| `probe.py` → "miniscore not found" | Cricbuzz ne redesign kar diya. `scraper.py` ke `extract_object` / `TEAM_RE` patterns update karne honge. |
| Overlay blank / "RECONNECTING" | Host galat hai ya server so gaya. `/api/health` curl karke dekho. |
| Animation chal rahi hai par awaz nahi | `?auto=1` hatakar ek baar tap karo. Phir Prism me **Internal Audio** on hai ya nahi check karo. |
| Awaz aati hai par YouTube pe nahi | Prism ScreenCast ke Audio settings me device/internal audio volume 100% karo. |
| Hindi commentary nahi, sirf English | `edge-tts` install nahi hua ya network block hai → `/api/health` me `"tts": false` dikhega. |
| Score update nahi ho raha | Match khatam ho gaya ya abhi live nahi hai. `/api/matches` se naya live match chuno. |
| Bahut zyada delay | `poll_seconds` 5 se 4 karo. 3 se neeche mat jao. |
| Pehli request slow (cold start) | Render free tier ki wajah se. Match se 10 min pehle overlay khol lo. |

---

## 🧪 Local test bina live match ke

Koi match live nahi hai to bhi system test kar sakte ho:

```bash
python main.py --rehearse      # synthetic feed: animation + Hindi TTS + over cards
```

Ya browser me HTML file direct kholo (`overlay/index.html`) — **demo mode**
khud chal jayega, koi server nahi chahiye.

---

## ⚠️ Legal — zaroor padho

- **Cricbuzz ke Terms of Service** public scraping allow nahi karte. Ye tool
  personal/rehearsal use aur apne local matches ke liye bana hai.
- **Cricket broadcast footage, commentary text aur team logos** Star Sports /
  JioHotstar / ICC ke copyright me hain. Sirf commentary ke *paraphrased*
  Hindi audio ke saath bhi **Content ID claim ya strike** aa sakta hai.
- Agar aap monetize karna chahte ho to **official data feed** lo (Sportradar,
  CricAPI paid plans) aur apni khud ki graphics/commentary use karo.
- Apne johkim par use karein.

---

## 📁 Files

| File | Kaam |
|---|---|
| `main.py` | orchestrator + aiohttp HTTP/WebSocket server |
| `scraper.py` | Cricbuzz RSC payload parser + ball detector |
| `classifier.py` | commentary → SHOT_* / RUN_* / OUT_* |
| `commentary.py` | English → Hindi (template ya LLM) |
| `audio_engine.py` | edge-tts → MP3 cache + asset management |
| `probe.py` | endpoint + pipeline self-test |
| `overlay/index.html` | 1920×1080 broadcast overlay (canvas + UI + audio) |
| `config.yaml` | sab settings |
| `render.yaml` / `Procfile` / `Dockerfile` | deployment |
