"""
deploy_remote.py — main (agent) ye script chalata hoon.

Jab user GitHub + Render ke tokens de deta hai, ye script:
  1. GitHub repo banata hai
  2. Poora source tree push karta hai (folder structure ke saath — web-upload
     wali flattening problem yahan hai hi nahi)
  3. Render service create karta hai (API) + env vars set karta hai
  4. Deploy trigger karke build complete hone tak wait karta hai
  5. Final URL + health check print karta hai

Chalane ka tareeka:
    export GITHUB_TOKEN=github_pat_xxxxx
    export RENDER_API_KEY=rnd_xxxxx
    python deploy_remote.py

    # optional
    python deploy_remote.py --repo cricket-2d-live --region singapore --private
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))

GITHUB_API = "https://api.github.com"
RENDER_API = "https://api.render.com/v1"

# Render region closest to India
DEFAULT_REGION = "singapore"

ENV_VARS = [
    {"key": "PORT", "value": "8080"},
    {"key": "PYTHON_VERSION", "value": "3.11.9"},
    {"key": "BRAND_CHANNEL", "value": "SPORTS GYAN"},
    {"key": "BRAND_HANDLE", "value": "@sportsgyanin"},
    {"key": "BRAND_LOGO", "value": "SG"},
    {"key": "BRAND_ACCENT", "value": "#1de9b6"},
    {"key": "BRAND_TAGLINE", "value": "AUTO 2D ENGINE"},
    {"key": "MATCH_ID", "value": "auto"},
    {"key": "POLL_SECONDS", "value": "5"},
]

SKIP_DIRS = {"__pycache__", ".git", "deploy", ".venv", "node_modules", ".idea"}
SKIP_FILES = {".gitignore", "deploy_remote.py", "build_deploy.py"}


# ---------------------------------------------------------------------------
def log(msg=""):
    print(msg, flush=True)


def step(n, total, msg):
    log("\n" + "─" * 68)
    log("  [%d/%d] %s" % (n, total, msg))
    log("─" * 68)


def api(url, method="GET", token=None, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "cricket2d-deploy")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"message": raw[:600]}
    except Exception as e:
        return 0, {"message": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------------------
def copy_tree(dest: str):
    """Source tree ko temp dir me copy karo (junk hata kar)."""
    os.makedirs(dest, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, ROOT)
        if rel == ".":
            rel = ""
        for f in filenames:
            if f in SKIP_FILES:
                continue
            if f.endswith(".pyc") or f.endswith(".mp3"):
                continue
            src = os.path.join(dirpath, f)
            dst = os.path.join(dest, rel, f) if rel else os.path.join(dest, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    # runtime folders (git empty folders track nahi karta, to README rakho)
    for d in ("cache/tts", "assets/bgm", "assets/sfx"):
        p = os.path.join(dest, d)
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, ".gitkeep"), "w").close()
    return dest


def run(cmd, cwd=None, env=None, check=True):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True)
    if check and r.returncode != 0:
        log("   CMD FAILED: %s" % " ".join(cmd))
        log("   stdout: %s" % r.stdout[-1500:])
        log("   stderr: %s" % r.stderr[-1500:])
        raise SystemExit(1)
    return r


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="cricket-2d-live")
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--skip-render", action="store_true", help="sirf GitHub push, Render manual")
    ap.add_argument("--skip-github", action="store_true", help="repo already pushed, sirf Render")
    ap.add_argument("--repo-full", default="", help="owner/repo jab --skip-github ho")
    ap.add_argument("--github-token", default=os.getenv("GITHUB_TOKEN", ""))
    ap.add_argument("--render-key", default=os.getenv("RENDER_API_KEY", ""))
    args = ap.parse_args()

    if args.skip_github:
        args.github_token = args.github_token or ""
    if not args.skip_github and not args.github_token:
        log("ERROR: GITHUB_TOKEN nahi mila.")
        log("  export GITHUB_TOKEN=github_pat_xxxxxxxx")
        return 2

    total = 6 if not args.skip_render else 3

    html_url = ""

    # ---------- 1. whoami ------------------------------------------------
    if args.skip_github:
        log("\n  [--skip-github] GitHub steps chhod rahe hain")
        html_url = args.repo_full or ("https://github.com/" + args.repo)
        if "/" not in html_url.replace("https://github.com/", ""):
            log("   ERROR: --repo-full owner/repo chahiye, jaise deepakyadavg69/cricket-2d-live")
            return 2
        log("   using repo: %s" % html_url)
    else:
      step(1, total, "GitHub authentication check")
      st, me = api(GITHUB_API + "/user", token=args.github_token)
      if st != 200:
          log("   FAILED (%s): %s" % (st, me.get("message")))
          log("   -> Token galat hai ya expire ho gaya.")
          return 2
      user = me.get("login")
      log("   ✓ logged in as: %s" % user)

      # ---------- 2. repo ---------------------------------------------------
      step(2, total, "GitHub repo '%s' ready kar raha hoon" % args.repo)
      st, repo = api(GITHUB_API + "/repos/%s/%s" % (user, args.repo), token=args.github_token)
      if st == 200:
          log("   ✓ repo pehle se hai: %s" % repo["html_url"])
      else:
          st, repo = api(
              GITHUB_API + "/user/repos", "POST", args.github_token,
              {"name": args.repo, "private": args.private, "auto_init": False,
               "description": "Automated live cricket 2D broadcast engine"},
          )
          if st not in (200, 201):
              log("   FAILED (%s): %s" % (st, repo.get("message")))
              log("   -> Fine-grained token me 'Administration: Read+Write' chahiye.")
              return 2
          log("   ✓ repo ban gaya: %s" % repo["html_url"])

      clone_url = repo["clone_url"]
      html_url = repo["html_url"]

      # ---------- 3. push ---------------------------------------------------
      step(3, total, "Source tree push kar raha hoon (folders ke saath)")
      work = "/tmp/deploy_push_%d" % int(time.time())
      if os.path.exists(work):
          shutil.rmtree(work)
      copy_tree(work)
      n = sum(len(f) for _, _, f in os.walk(work))
      log("   %d files copy kiye" % n)

      run(["git", "init", "-b", "main"], cwd=work)
      run(["git", "config", "user.email", "deploy@local"], cwd=work)
      run(["git", "config", "user.name", "Cricket2D Deploy"], cwd=work)
      run(["git", "add", "-A"], cwd=work)
      run(["git", "commit", "-m", "Live Cricket 2D Engine - initial deploy"], cwd=work,
          check=False)

      push_url = clone_url.replace("https://", "https://%s@" % args.github_token)
      r = run(["git", "push", "-u", push_url, "main", "--force"], cwd=work, check=False)
      if r.returncode != 0:
          log("   push failed — retry with explicit refspec…")
          r = run(["git", "push", push_url, "HEAD:refs/heads/main", "--force"],
                  cwd=work, check=False)
      if r.returncode != 0:
          log("   FAILED: %s" % (r.stderr[-800:] or r.stdout[-800:]))
          return 2
      log("   ✓ push ho gaya")
      r = run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=work)
      for line in r.stdout.strip().split("\n")[:20]:
          log("       %s" % line)
      if r.stdout.count("\n") > 20:
          log("       … aur %d files" % (r.stdout.count("\n") - 20))

    if args.skip_render:
        log("\n" + "=" * 68)
        log("  DONE (GitHub only)")
        log("  Repo: %s" % html_url)
        log("  Ab Render dashboard → New + → Blueprint → ye repo select karo.")
        log("=" * 68)
        return 0

    # ---------- 4. Render owner -------------------------------------------
    if not args.render_key:
        log("\nERROR: RENDER_API_KEY nahi mila.")
        log("  Repo push ho chuka hai: %s" % html_url)
        log("  Ab Render dashboard → New + → Blueprint → repo select karo (5 click).")
        log("  Ya: export RENDER_API_KEY=rnd_xxxxx && python deploy_remote.py --skip-github")
        return 2

    step(4, total, "Render account check")
    st, owners = api(RENDER_API + "/owners", token=args.render_key)
    if st != 200 or not owners:
        log("   FAILED (%s): %s" % (st, owners.get("message")))
        return 2
    owner = owners[0].get("owner", owners[0])
    owner_id = owner.get("id")
    log("   ✓ owner: %s (%s)" % (owner.get("name"), owner_id))

    # ---------- 5. service ------------------------------------------------
    step(5, total, "Render service create kar raha hoon")
    body = {
        "type": "web_service",
        "name": args.repo,
        "ownerId": owner_id,
        "repo": html_url,
        "branch": "main",
        "autoDeploy": "yes",
        "serviceDetails": {
            "runtime": "python",
            "plan": "free",
            "region": args.region,
            "numInstances": 1,
            "healthCheckPath": "/api/health",
            "envVars": ENV_VARS,
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "python main.py",
            },
        },
    }
    st, svc = api(RENDER_API + "/services", "POST", args.render_key, body)
    if st not in (200, 201):
        log("   FAILED (%s): %s" % (st, json.dumps(svc)[:700]))
        log("")
        log("   Fallback — Render dashboard se 5 click me kar lo:")
        log("     1. dashboard.render.com → New + → Blueprint")
        log("     2. repo chuno: %s" % html_url)
        log("     3. Apply")
        log("   (render.yaml repo me maujood hai, sab auto-configure ho jayega)")
        return 3

    svc_id = svc.get("service", {}).get("id") or svc.get("id")
    svc_url = svc.get("service", {}).get("serviceDetails", {}).get("url") or ""
    log("   ✓ service created: %s" % svc_id)

    # ---------- 6. wait for build ------------------------------------------
    step(6, total, "Build complete hone ka wait (3-6 min)…")
    url = ""
    for i in range(60):
        st, d = api(RENDER_API + "/services/%s" % svc_id, token=args.render_key)
        if st == 200:
            s = d.get("service", d)
            url = (s.get("serviceDetails") or {}).get("url") or s.get("url") or url
            state = s.get("state") or (s.get("serviceDetails") or {}).get("state")
            if i % 4 == 0:
                log("   [%3ds] state=%s" % (i * 8, state))
            if state == "live":
                break
            if state in ("build_failed", "deploy_failed", "canceled"):
                log("   BUILD FAILED: %s" % state)
                log("   Logs: https://dashboard.render.com/web/%s" % svc_id)
                return 4
        time.sleep(8)

    # ---------- health ------------------------------------------------------
    base = url or ("https://%s.onrender.com" % args.repo)
    log("\n" + "=" * 68)
    log("  🎉 DEPLOY COMPLETE")
    log("=" * 68)
    log("  URL       : %s" % base)
    log("  Admin     : %s/admin" % base)
    log("  Health    : %s/api/health" % base)
    log("  Overlay   : %s/?auto=1" % base)
    log("  Repo      : %s" % html_url)
    log("")
    log("  Health check kar raha hoon (cold start ~40s)…")
    for i in range(12):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=25) as r:
                d = json.loads(r.read().decode())
                log("  ✓ %s" % json.dumps({
                    "scraper": d.get("scraper"),
                    "tts": (d.get("assets") or {}).get("tts"),
                    "mode": (d.get("status") or {}).get("mode"),
                    "match": (d.get("match") or {}).get("title"),
                }))
                break
        except Exception as e:
            log("     … attempt %d (%s)" % (i + 1, type(e).__name__))
            time.sleep(10)
    log("")
    log("  Ab tokens delete kar do:")
    log("    GitHub : github.com/settings/tokens")
    log("    Render : dashboard.render.com → Account Settings → API Keys")
    log("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
