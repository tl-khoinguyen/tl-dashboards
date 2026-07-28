#!/usr/bin/env python3
"""Build dashboard HTML pages from template + config + pulled cache.
Generic: merges config (per-project rules) with data (RAW/SMAP) into the
template's DATA slot. Outputs a Pages-ready site tree:
  <outdir>/<slug>/index.html   one page per project
  <outdir>/all/index.html      aggregate page (project tabs)
  <outdir>/index.html          redirect -> all/
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
a = ap.parse_args()
if a.snapshot is not None and (a.enc or a.enc_env):
    sys.exit("--snapshot is always plaintext — drop --enc/--enc-env")

CFG = json.load(open(a.config, encoding="utf-8"))
D = json.load(open(a.data, encoding="utf-8"))
TPL = open(a.tpl, encoding="utf-8").read()
PASS = a.enc or (os.environ.get(a.enc_env, "") if a.enc_env else None)
if a.enc_env and not PASS:
    sys.exit(f"--enc-env {a.enc_env}: env var empty/unset")

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
    return {"slug": slug, "name": pc.get("name", pc["key"]),
        "conf": {"space": SPACE, "pk": pc["key"]},
        "SMAP": p["SMAP"], "RAW": p["RAW"],
        "cfg": {"defaultScope": pc.get("defaultScope", "all"),
                "tagScopes": pc.get("tagScopes", []),
                "categoryMode": pc.get("categoryMode", "issueType"),
                "catNormalize": pc.get("catNormalize", {}),
                "thresholds": pc.get("thresholds", {}),
                "note": pc.get("note", None)}}

def encrypt(obj, passphrase):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import hashlib
    salt = os.urandom(16); iv = os.urandom(12); iters = 200000
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iters, 32)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    b64 = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "salt": b64(salt), "iv": b64(iv), "iter": iters, "ct": b64(ct)}

def emit(page_slug, projs):
    if PASS:
        enc = encrypt({"projects": projs}, PASS)
        data = (f"const META={json.dumps(META)};\nconst BUILD={json.dumps(BUILD)};\n"
                f"const ENC={json.dumps(enc)};\nlet PROJECTS=null;")
    else:
        data = (f"const META={json.dumps(META)};\nconst BUILD={json.dumps(BUILD)};\n"
                f"let PROJECTS={json.dumps(projs, ensure_ascii=False)};")
    html = TPL.replace("/*__DATA__*/", data)
    d = os.path.join(a.outdir, page_slug); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "index.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"WROTE {out} {round(len(html)/1024)}KB enc={'yes' if PASS else 'NO(plaintext)'}")

if a.snapshot is not None:
    # quick CEO snapshot — one self-contained file, all projects, plaintext
    projs = [payload(pc) for pc in CFG["projects"]]
    data = (f"const META={json.dumps(META)};\nconst BUILD={json.dumps(BUILD)};\n"
            f"let PROJECTS={json.dumps(projs, ensure_ascii=False)};")
    html = TPL.replace("/*__DATA__*/", data)
    out = a.snapshot or os.path.join(HERE, "..", "snapshots", f"{META['generated'].replace('-', '.')}-snapshot.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(html)
    print(f"WROTE {out} {round(len(html)/1024)}KB — PLAINTEXT all-in-one snapshot: private send only, never deploy")
    sys.exit(0)

only = a.only.split(",") if a.only else None
pcs = [pc for pc in CFG["projects"] if not only or pc["slug"] in only]
for pc in pcs:
    emit(pc["slug"], [payload(pc)])
if not only or "all" in only:
    emit("all", [payload(pc) for pc in CFG["projects"]])
    root = os.path.join(a.outdir, "index.html")
    open(root, "w", encoding="utf-8").write(
        '<!doctype html><meta http-equiv="refresh" content="0; url=all/">'
        '<a href="all/">→</a>')
    print(f"WROTE {root} (redirect -> all/)")
