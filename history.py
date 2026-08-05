#!/usr/bin/env python3
"""Accumulate a per-day aggregate history from the pulled cache (DP-02, 2026-08-05).
Each build upserts TODAY's per-project aggregate counts into an encrypted history
file that CI commits back to the repo — the raw pull is ephemeral, so real trends
(open-count series, CFD, sparklines, period comparison) can only exist if every
build leaves this small trace behind. Two builds a day simply overwrite the same
date row; last write wins with the fuller picture.
Counts only: status/category/type tallies + that day's created/closed. No ticket
titles, no person names. Encrypted with the same v2 envelope as the pages
(AES-GCM under a DEK, DEK wrapped per passphrase slot) because the file sits in a
public repo and category/type vocabulary is internal.
Safety: an existing file that fails to decrypt ABORTS the run (never clobber
accumulated history) — use --reset to knowingly start over.
Side output: the decrypted JSON goes to cache/history.json (gitignored) so a
later build step can embed it into pages without re-decrypting.
Usage: history.py --config config.json --data cache/data.json
                  --file history.enc (--enc PASS | --enc-env VAR)
"""
import os, json, sys, re, argparse, base64, hashlib
sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--config", default=os.path.join(HERE, "..", "config", "projects.json"))
ap.add_argument("--data", default=os.path.join(HERE, "cache", "data.json"))
ap.add_argument("--file", default=os.path.join(HERE, "..", "history.enc"))
ap.add_argument("--enc", default=None, help="passphrase")
ap.add_argument("--enc-env", default=None, help="read passphrase from this env var")
ap.add_argument("--plain-out", default=os.path.join(HERE, "cache", "history.json"),
                help="decrypted copy for same-run consumers (gitignored dir)")
ap.add_argument("--reset", action="store_true",
                help="start a fresh history even if the existing file won't decrypt")
a = ap.parse_args()

PASS = a.enc or (os.environ.get(a.enc_env, "") if a.enc_env else None)
if not PASS:
    sys.exit("history.py: passphrase required (--enc or --enc-env) — plaintext history is never written to the repo")

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
b64 = lambda x: base64.b64encode(x).decode()
ub64 = base64.b64decode

def encrypt(obj, pw):
    dek = os.urandom(32); iv = os.urandom(12)
    ct = AESGCM(dek).encrypt(iv, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), None)
    salt = os.urandom(16); iv2 = os.urandom(12); iters = 200000
    kek = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters, 32)
    slots = [{"salt": b64(salt), "iv": b64(iv2), "iter": iters,
              "wk": b64(AESGCM(kek).encrypt(iv2, dek, None))}]
    return {"v": 2, "iv": b64(iv), "ct": b64(ct), "slots": slots}

def decrypt(env, pw):
    for s in env.get("slots", []):
        try:
            kek = hashlib.pbkdf2_hmac("sha256", pw.encode(), ub64(s["salt"]), int(s["iter"]), 32)
            dek = AESGCM(kek).decrypt(ub64(s["iv"]), ub64(s["wk"]), None)
            return json.loads(AESGCM(dek).decrypt(ub64(env["iv"]), ub64(env["ct"]), None))
        except Exception:
            continue
    return None

CFG = json.load(open(a.config, encoding="utf-8"))
D = json.load(open(a.data, encoding="utf-8"))
TODAY = D["today"]

hist = {"v": 1, "days": {}}
if os.path.exists(a.file):
    prev = decrypt(json.load(open(a.file, encoding="utf-8")), PASS)
    if prev is None:
        if not a.reset:
            sys.exit(f"history.py: {a.file} exists but does not decrypt with this passphrase — refusing to overwrite (use --reset to start over)")
        print("RESET — starting a fresh history over an undecryptable file")
    else:
        hist = prev

def subber(pc):
    """Issue -> category key, replicating the template's sub() (categoryMode)."""
    mode = pc.get("categoryMode", "issueType")
    norm = pc.get("catNormalize", {})
    if mode == "bracket2":
        def f(i):
            m = re.findall(r"【([^】]+)】", i.get("summary") or "")
            return norm.get(m[1], m[1]) if len(m) > 1 else None
        return f
    if mode == "issueType":
        return lambda i: i.get("type") or None
    return lambda i: None

def day_row(pc, pull):
    smap = pull["SMAP"]
    bkt = lambda i: smap.get(str(i.get("stId")), "Open")
    raw = pull["RAW"]
    sub = subber(pc)
    is_open = lambda i: bkt(i) != "Closed"
    opens = [i for i in raw if is_open(i)]
    row = {"open": len(opens),
           "bkt": {}, "st": {}, "cat": {}, "typ": {},
           "overdue": 0, "stale30": 0,
           "created": sum(1 for i in raw if (i.get("created") or "") == TODAY),
           "closed": sum(1 for i in raw if not is_open(i) and (i.get("updated") or "") == TODAY)}
    def cutoff(days):
        from datetime import date, timedelta
        y, m, d = map(int, TODAY.split("-"))
        return (date(y, m, d) - timedelta(days=days)).isoformat()
    stale_lim = cutoff(30)
    for i in opens:
        row["bkt"][bkt(i)] = row["bkt"].get(bkt(i), 0) + 1
        sid = str(i.get("stId"))
        row["st"][sid] = row["st"].get(sid, 0) + 1
        c = sub(i)
        if c: row["cat"][c] = row["cat"].get(c, 0) + 1
        t = i.get("type")
        if t: row["typ"][t] = row["typ"].get(t, 0) + 1
        if i.get("dueDate") and i["dueDate"] < TODAY: row["overdue"] += 1
        if i.get("updated") and i["updated"] < stale_lim: row["stale30"] += 1
    return row

day = hist["days"].setdefault(TODAY, {})
for pc in CFG["projects"]:
    slug = pc["slug"]
    if slug not in D["pulls"]:
        print(f"SKIP {slug} — no pulled data"); continue
    day[slug] = day_row(pc, D["pulls"][slug])
    r = day[slug]
    print(f"{slug}: open={r['open']} overdue={r['overdue']} stale30={r['stale30']} created={r['created']} closed={r['closed']} cats={len(r['cat'])}")

os.makedirs(os.path.dirname(a.plain_out) or ".", exist_ok=True)
json.dump(hist, open(a.plain_out, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(encrypt(hist, PASS), open(a.file, "w", encoding="utf-8"))
sz = os.path.getsize(a.file)
print(f"WROTE {a.file} ({round(sz/1024,1)}KB, {len(hist['days'])} day(s) accumulated) + {a.plain_out} (plaintext, gitignored)")
