#!/usr/bin/env python3
"""Build dashboard HTML pages from template + config + pulled cache.
Generic: merges config (per-project rules) with data (RAW/SMAP) into the
template's DATA slot. Outputs a Pages-ready site tree:
  <outdir>/<slug>/index.html   one page per project
  <outdir>/all/index.html      aggregate page (project tabs)
  <outdir>/index.html          redirect -> all/
Site layout (07.28 rev2 — audience separation): /all/ = operator+CEO page (tabs,
#slug hash routing); /<slug>/ = STANDALONE per-project page (own data only, no
tabs, no link to other projects) shareable to that project's stakeholders.
Per-page passphrases: a project with "passphrase" in its config entry gets its
page encrypted with THAT passphrase — its stakeholders cannot open /all or other
pages (crypto-scoped access without a user system). No per-project passphrase →
the global one. /all always uses the global.
Encryption: --enc PASSPHRASE or --enc-env VAR (e.g. DASH_PASSPHRASE).
Plaintext build (no --enc*) is for private ad-hoc snapshots only — never deploy.
Quick CEO snapshot: --snapshot [PATH] writes ONE all-in-one self-contained HTML
(all projects, tabs, always plaintext — encryption flags rejected). Default PATH
= ../snapshots/YYYY.MM.DD-snapshot.html. Send privately only.
Usage: build.py [--config ...] [--data cache/data.json] [--outdir dist]
                [--only SLUG[,SLUG]] [--built YYYY-MM-DD] [--snapshot [PATH]]
"""
import os, json, sys, argparse, base64
sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--config", default=os.path.join(HERE, "..", "config", "projects.json"))
ap.add_argument("--data", default=os.path.join(HERE, "cache", "data.json"))
ap.add_argument("--tpl", default=os.path.join(HERE, "template.html"))
ap.add_argument("--outdir", default=os.path.join(HERE, "dist"))
ap.add_argument("--only", default=None, help="comma-separated slugs (skip 'all' unless listed)")
ap.add_argument("--built", default=None, help="override META.generated (demo staleness)")
ap.add_argument("--enc", default=None, help="passphrase -> encrypt payload")
ap.add_argument("--enc-env", default=None, help="read passphrase from this env var")
ap.add_argument("--snapshot", nargs="?", const="", default=None,
                help="quick all-in-one single-file snapshot (always plaintext)")
ap.add_argument("--insight", default=None,
                help="weekly AI-insight JSON; default: cache/insight.json (CI secret channel) "
                     "else newest ../insights/*.json — absent = section hidden")
a = ap.parse_args()
if a.snapshot is not None and (a.enc or a.enc_env):
    sys.exit("--snapshot is always plaintext — drop --enc/--enc-env")

CFG = json.load(open(a.config, encoding="utf-8"))
D = json.load(open(a.data, encoding="utf-8"))
TPL = open(a.tpl, encoding="utf-8").read()
PASS = a.enc or (os.environ.get(a.enc_env, "") if a.enc_env else None)
if a.enc_env and not PASS:
    sys.exit(f"--enc-env {a.enc_env}: env var empty/unset")

def load_insight():
    """Weekly AI insight (optional — dashboard hides the section when absent).
    Operator-reviewed before publish; travels to CI as Secret DASH_INSIGHT_B64
    materialized to cache/insight.json. Locally: newest file in ../insights/."""
    import glob
    cands = [a.insight] if a.insight else [os.path.join(HERE, "cache", "insight.json")]
    if not a.insight:
        files = sorted(glob.glob(os.path.join(HERE, "..", "insights", "*.json")))
        if files: cands.append(files[-1])
    for c in cands:
        if c and os.path.exists(c):
            ins = json.load(open(c, encoding="utf-8"))
            print(f"INSIGHT {c} (dated {ins.get('date')})")
            return ins
    print("INSIGHT none — section hidden")
    return None

INS = load_insight()

META = {"today": D["today"], "generated": a.built or D["generated"]}
BUILD = {"updateUrl": CFG.get("updateUrl", "")}
SPACE = D.get("space", CFG.get("space", ""))

def payload(pc):
    """One project's full payload object: data + the per-project rules the
    template needs. Company-identifying parts (space/pk/names) travel here —
    encrypted in deploy builds."""
    slug = pc["slug"]
    if slug not in D["pulls"]:
        sys.exit(f"no pulled data for slug '{slug}' — run pull.py")
    p = D["pulls"][slug]
    ins = None
    if INS and slug in INS.get("pages", {}):
        ins = {"date": INS.get("date"), "week": INS.get("week"),
               "items": INS["pages"][slug]}
    return {"slug": slug, "name": pc.get("name", pc["key"]),
        "conf": {"space": SPACE, "pk": pc["key"], "pid": p.get("pid")},
        "SMAP": p["SMAP"], "RAW": p["RAW"], "insight": ins,
        "statuses": p.get("statuses", []), "colors": p.get("colors", {}),
        "cfg": {"defaultScope": pc.get("defaultScope", "all"),
                "areas": pc.get("areas"),
                "tagScopes": pc.get("tagScopes", []),
                "categoryMode": pc.get("categoryMode", "issueType"),
                "catNormalize": pc.get("catNormalize", {}),
                "thresholds": pc.get("thresholds", {}),
                "note": pc.get("note", None)}}

def encrypt(obj, passes):
    """Envelope (v2, 07.28): payload under a random DEK; the DEK wrapped once per
    passphrase ('key slots'). Master passphrase opens every page; a per-project
    passphrase opens only its page. Any listed passphrase decrypts client-side."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import hashlib
    b64 = lambda x: base64.b64encode(x).decode()
    dek = os.urandom(32); iv = os.urandom(12)
    ct = AESGCM(dek).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    slots = []
    for pw in passes:
        salt = os.urandom(16); iv2 = os.urandom(12); iters = 200000
        kek = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters, 32)
        slots.append({"salt": b64(salt), "iv": b64(iv2), "iter": iters,
                      "wk": b64(AESGCM(kek).encrypt(iv2, dek, None))})
    return {"v": 2, "iv": b64(iv), "ct": b64(ct), "slots": slots}

def emit(page_slug, projs, pw=None, cross=None):
    # master passphrase always unlocks; a per-project one adds a second key slot
    # cross = /all-only cross-project insight block (operator+CEO audience)
    passes = [p for p in dict.fromkeys([PASS, pw]) if p]
    if passes:
        enc = encrypt({"projects": projs, "cross": cross}, passes)
        data = (f"const META={json.dumps(META)};\nconst BUILD={json.dumps(BUILD)};\n"
                f"const ENC={json.dumps(enc)};\nlet PROJECTS=null;\nlet CROSS=null;")
    else:
        data = (f"const META={json.dumps(META)};\nconst BUILD={json.dumps(BUILD)};\n"
                f"let PROJECTS={json.dumps(projs, ensure_ascii=False)};\n"
                f"let CROSS={json.dumps(cross, ensure_ascii=False)};")
    html = TPL.replace("/*__DATA__*/", data)
    d = os.path.join(a.outdir, page_slug); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "index.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"WROTE {out} {round(len(html)/1024)}KB enc={f'yes({len(passes)} slot)' if passes else 'NO(plaintext)'}")

if a.snapshot is not None:
    # quick CEO snapshot — one self-contained file, all projects, plaintext
    projs = [payload(pc) for pc in CFG["projects"]]
    data = (f"const META={json.dumps(META)};\nconst BUILD={json.dumps(BUILD)};\n"
            f"let PROJECTS={json.dumps(projs, ensure_ascii=False)};\n"
            f"let CROSS={json.dumps(INS.get('cross') if INS else None, ensure_ascii=False)};")
    html = TPL.replace("/*__DATA__*/", data)
    out = a.snapshot or os.path.join(HERE, "..", "snapshots", f"{META['generated'].replace('-', '.')}-snapshot.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(html)
    print(f"WROTE {out} {round(len(html)/1024)}KB — PLAINTEXT all-in-one snapshot: private send only, never deploy")
    sys.exit(0)

def stub(path, url):
    d = os.path.dirname(path); os.makedirs(d, exist_ok=True)
    open(path, "w", encoding="utf-8").write(
        f'<!doctype html><meta http-equiv="refresh" content="0; url={url}">'
        f'<a href="{url}">→</a>')
    print(f"WROTE {path} (redirect -> {url})")

only = a.only.split(",") if a.only else None
for pc in CFG["projects"]:
    if only and pc["slug"] not in only: continue
    # standalone page, optionally under its own passphrase (stakeholder scope)
    emit(pc["slug"], [payload(pc)], pw=pc.get("passphrase"))
if not only or "all" in only:
    emit("all", [payload(pc) for pc in CFG["projects"]],
         cross=INS.get("cross") if INS else None)
    stub(os.path.join(a.outdir, "index.html"), "all/")
