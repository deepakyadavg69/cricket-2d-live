# 🚀 Render.com pe deploy kaise karein — tablet/phone se (step by step)

Ye guide specially isliye likhi hai kyunki aapke paas **sirf tablet aur mobile** hain.
Koi PC, koi terminal, koi `git` command nahi chahiye.

**Kul samay: ~25 minute** (zyada tar wait karna)

---

## 📥 Step 0 — 4 files download kar lo

Workspace me ye folder hai: **`cricket-live/deploy/`**

In 4 files ko apne tablet me download kar lo (Downloads folder me):

| File | Size |
|---|---|
| `app.py` | ~255 KB ← poori project isi ke andar hai |
| `requirements.txt` | 1 KB |
| `render.yaml` | 1 KB |
| `Procfile` | 1 KB |

> **Sirf ye 4.** Baaki saare files (`main.py`, `scraper.py`, `overlay/index.html`…)
> `app.py` ke andar base64 me packed hain — wo Render pe chalte waqt khud
> extract ho jayenge, **sahi folder structure ke saath**.
>
> Kyun? Kyunki GitHub ka web upload folders ko flatten kar deta hai —
> `overlay/index.html` seedha root pe `index.html` ban jata aur server 404 deta.

---

## 🐙 Step 1 — GitHub account banao

1. Chrome kholo → **github.com**
2. ⚠️ Pehle **⋮ (menu) → "Desktop site"** ON kar lo — mobile view me upload UI theek kaam nahi karta
3. **Sign up** → email, password, username
4. Email verify kar do

---

## 📁 Step 2 — Naya repository banao

1. GitHub pe upar right me **+ → New repository**
2. **Repository name:** `cricket-2d-live`
3. **Public** select karo (free Render deploys ke liye private bhi chalega, par public simple hai)
4. ✅ **"Add a README file"** ko tick karo
5. **Create repository** dabao

---

## ⬆️ Step 3 — 4 files upload karo

1. Repo khul gaya → **"Add file"** button (green) → **"Upload files"**
2. **"choose your files"** dabao → Downloads me jao → **4 files select karo** (ek saath select ho sakte hain)
3. Neeche **Commit changes** → **Commit directly to the main branch** → **Commit changes**

Ho gaya. Ab repo me 5 files dikhne chahiye: `README.md`, `app.py`, `requirements.txt`, `render.yaml`, `Procfile`

---

## ☁️ Step 4 — Render account + deploy

1. Chrome me naya tab → **render.com** → **Get Started**
2. **"Sign up with GitHub"** chuno (isse Render ko repo dikh jayega)
3. Authorize kar do
4. Dashboard → upar right **"New +"** → **"Blueprint"**
5. Apna repo `cricket-2d-live` select karo → **Connect**
   - Agar nahi dikhe to **"Configure account"** pe click karke access do
6. Render `render.yaml` padh lega. Dikhega:
   ```
   Service name : cricket-2d-live
   Runtime      : Python 3
   Plan         : Free
   Region       : Singapore
   Build        : pip install -r requirements.txt
   Start        : python app.py
   ```
7. **Apply** / **Deploy** dabao

⏳ **Pehli build me 3–5 minute** lagenge (aiohttp + edge-tts install hote hain).

Jab upar **"Live"** (green) likha aaye → tayyar hai.

---

## 🌐 Step 5 — Apna URL note kar lo

Upar service name ke neeche milega:

```
https://cricket-2d-live.onrender.com
```

**Ise kahin note kar lo.** Aage har jagah yahi lagega.

---

## ✅ Step 6 — Test karo (tablet ke Chrome me)

Ek-ek karke ye URLs kholo:

| URL | Kya dikhna chahiye |
|---|---|
| `https://cricket-2d-live.onrender.com/api/health` | `{"ok": true, "status": {...}}` — `"tts": true` hona chahiye |
| `https://cricket-2d-live.onrender.com/admin` | **Control page** — match picker (ye sabse important hai) |
| `https://cricket-2d-live.onrender.com/` | **1920×1080 overlay** — ground, scorebar, commentary |

Agar `/` pe overlay dikhe aur `/admin` khul jaye → **deploy successful** 🎉

---

## ⚙️ Step 7 — Apni settings daalo (bina file edit kiye)

Render dashboard → apna service → left me **"Environment"** → **"Add Environment Variable"**

Ye daalo:

| Key | Value | Matlab |
|---|---|---|
| `BRAND_CHANNEL` | `APNA SPORTS` | overlay me neeche dikhega |
| `BRAND_HANDLE` | `@apnasports` | aapka YouTube handle |
| `BRAND_LOGO` | `A` | logo box me 1 akshar |
| `BRAND_ACCENT` | `#ff9d3d` | overlay ka rang (hex) |
| `MATCH_ID` | `auto` | ya koi specific id |

Har variable add karne ke baad Render **khud redeploy** karega (~1 min).

> 💡 `MATCH_ID=auto` rehne do — server khud pehla live match pakad lega.
> Specific match chahiye to `/admin` page se click karke badal sakte ho (Step 8).

---

## 🎯 Step 8 — Match kaise chuno (roz ka kaam)

Tablet ke Chrome me kholo:

```
https://cricket-2d-live.onrender.com/admin
```

- Upar **current status** dikhega (backend ok, TTS on, kitne clients)
- Neeche **saare live matches ki list** — jis par tap karna hai wo match set ho jayega
- Ya **Match ID** box me number daal ke **Set** dabao

Bas. Koi curl, koi terminal nahi.

---

## 📱 Step 9 — Stream kaise karein (Prism Live Studio)

> ⚠️ YouTube mobile live ke liye **50+ subscribers** aur **verified channel** zaroori hai.

1. **Chrome** (tablet) me kholo:
   ```
   https://cricket-2d-live.onrender.com/?auto=1
   ```
   - `?auto=1` = "TAP TO GO LIVE" wali screen skip
   - **⋮ → Full screen** kar do, rotation lock kar do
   - Agar **awaz nahi aa rahi**: `?auto=1` hatakar kholo aur screen par **ek baar tap** karo

2. **Prism Live Studio** kholo → **Live** → platform **YouTube**

3. Source chuno: **ScreenCast** (screen capture)
   ❌ **"Web" / webpage source mat use karna** — usme awaz capture nahi hoti

4. **Audio settings**:
   - **Internal sound / device audio** = 100%
   - **Mic** = 0%

5. **1080p**, bitrate **8000–12000 kbps**, landscape

6. **Go Live** 🚀

7. Tablet **charger pe rakho**, aur Chrome ko background-kill se bachao:
   `Settings → Apps → Chrome → Battery → Unrestricted`

---

## 🔥 Step 10 — Render free tier ko jagaye rakho

Free instance **15 minute tak koi request na aaye to so jata hai**.
Jab tak aapka Chrome overlay poll kar raha hai (har 5 second), ye jagta rahega.

Lekin **match se 10 minute pehle overlay khol lena** — warna pehli request me
cold-start ke ~40 second lagenge.

Extra safety chahiye to:
1. **cron-job.org** pe free account banao
2. New cronjob → URL: `https://cricket-2d-live.onrender.com/api/health`
3. Period: **every 5 minutes**
4. Save

Ab server kabhi nahi soyega.

---

## 🔁 Roz ka workflow (3 min)

```
1. /admin kholo            → live match select karo
2. Chrome me /?auto=1      → fullscreen
3. Prism Live → ScreenCast → Go Live
4. Match khatam            → /admin se agla match, ya Stream End
```

---

## 🆘 Troubleshooting

| Problem | Fix |
|---|---|
| Deploy fail: "Build failed" | Render → service → **Logs** kholo. Zyada tar `requirements.txt` missing hota hai (Step 3 me upload hua?) |
| `/api/health` me `"tts": false` | `edge-tts` install nahi hua. Logs me pip error dekho. Awaz nahi aayegi par animation chalegi |
| Overlay dikhta hai par blank/DEMO | `/api/health` check karo. Agar `scraper: parse_error` ho to Cricbuzz ne redesign kar diya — mujhe batao, parser update kar dunga |
| Overlay "RECONNECTING" | Server so gaya hai. `/api/health` ek baar kholo, 40s wait karo, phir reload |
| Animation chalti hai, awaz nahi | `?auto=1` hatakar ek baar tap karo. Phir Prism me **Internal Audio** 100% check karo |
| Awaz hai par YouTube pe nahi | Prism Live ke ScreenCast audio settings me device/internal audio on karo |
| Match galat hai / purana | `/admin` kholo, sahi match tap karo |
| `MATCH_ID` env set kiya par change nahi hua | Render env vars change karne pe redeploy hota hai — 1–2 min wait karo |
| Bahut delay hai | `POLL_SECONDS=3` env var add karo. 3 se kam mat karna (rate-limit) |

---

## 📞 Madad chahiye to

Render ke **Logs** tab ka screenshot ya `/api/health` ka output mujhe bhej do —
main exactly bata dunga kya gadbad hai.

Saath hi ye bhi batao:
- `/api/health` me `"scraper"` kya likha hai
- `"tts"` true hai ya false
- Console me koi error (Chrome me `chrome://inspect` se, ya overlay me **H** dabao)
