#!/usr/bin/env python3
"""Pull Backlog data for every project in the config -> cache JSON (data only).
Read-only. Generic: all project specifics come from the config file.
Creds: BACKLOG_API_KEY env (required); space from config or BACKLOG_SPACE env.
Usage: pull.py [--config ../config/projects.json] [--out cache/data.json]
"""
import os, json, sys, argparse, datetime, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--config", default=os.path.join(HERE, "..", "config", "projects.json"))
ap.add_argument("--out", default=os.path.join(HERE, "cache", "data.json"))
a = ap.parse_args()

CFG = json.load(open(a.config, encoding="utf-8"))
SPACE = (CFG.get("space") or os.environ.get("BACKLOG_SPACE", "")).strip()
KEY = os.environ["BACKLOG_API_KEY"].strip()
if not SPACE:
    sys.exit("no Backlog space (config.space or BACKLOG_SPACE)")
BASE = f"https://{SPACE}/api/v2"
# Anchor "today" to the operator's timezone (config tzOffsetHours), NOT the
# runner's local date — CI runs in UTC, so an early-morning build (before
# 07:00 UTC+7) would otherwise stamp yesterday's date and shift every
# range window by a day. Bit the 05:30 daily cron (found 2026-07-29).
_TZ = datetime.timezone(datetime.timedelta(hours=float(CFG.get("tzOffsetHours", 0))))
_TODAY = datetime.datetime.now(_TZ).date()
TODAY = _TODAY.isoformat()
WIDE = (_TODAY - datetime.timedelta(days=int(CFG.get("windowDays", 120)))).isoformat()

def get(path, params=None):
    params = dict(params or {}); params["apiKey"] = KEY; pairs = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            for it in v: pairs.append((k, it))
        else: pairs.append((k, v))
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(pairs)
    for att in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as r: return json.load(r)
        except Exception:
            if att == 3: raise

def paginate(path, params, label):
    out, off = [], 0
    while True:
        p = dict(params); p["count"] = 100; p["offset"] = off; b = get(path, p)
        if not b: break
        out.extend(b); sys.stderr.write(f"\r{label}:{len(out)}"); sys.stderr.flush()
        if len(b) < 100: break
        off += 100
    sys.stderr.write("\n"); return out

def bucket(st):
    """Heuristic status -> 5-bucket map. Built-in ids are locale-safe anchors
    (1=Open 2=InProgress 3=Resolved 4=Closed); custom statuses fall back to
    name keywords. Per-project `smap` in the config overrides any id."""
    i = st["id"]; n = st["name"]; nl = n.lower()
    if i == 4 or "完了" in n: return "Closed"
    if i == 1: return "Open"
    if i == 2: return "InProgress"
    if i == 3: return "Resolved"
    if "処理済" in n or "resolved" in nl or "confirmed" in nl: return "Resolved"
    if any(k in nl for k in ["qa", "test", "merge", "progress", "reopen", "verify", "レビュー", "review"]) or "処理中" in n: return "InProgress"
    if "pending" in nl or "保留" in n or "priority" in nl or "hold" in nl or "待ち" in n: return "Pending"
    if "未対応" in n or "open" in nl or "未着手" in n: return "Open"
    return "Open"

def slim(i):
    return {"key": i["issueKey"], "summary": i.get("summary", "") or "",
        "stId": (i.get("status") or {}).get("id"), "prId": (i.get("priority") or {}).get("id"),
        "type": (i.get("issueType") or {}).get("name"),
        "assignee": (i.get("assignee") or {}).get("name"), "aId": (i.get("assignee") or {}).get("id"),
        "created": (i.get("created") or "")[:10], "updated": (i.get("updated") or "")[:10],
        "dueDate": (i.get("dueDate") or "")[:10] or None,
        "milestone": [m["name"] for m in i.get("milestone", [])],
        "category": [c["name"] for c in i.get("category", [])], "parentId": i.get("parentIssueId")}

pulls = {}
for pc in CFG["projects"]:
    key, slug = pc["key"], pc["slug"]
    proj = get(f"projects/{key}"); PID = proj["id"]
    sts = get(f"projects/{PID}/statuses")
    SMAP = {str(s["id"]): bucket(s) for s in sts}
    SMAP.update({str(k): v for k, v in (pc.get("smap") or {}).items()})   # config overrides win
    # real per-project statuses (name/color straight from Backlog) + one representative
    # color per bucket (prefer the built-in status 1-4 of that bucket)
    STATUSES = [{"id": s["id"], "name": s["name"], "color": s.get("color")}
                for s in sorted(sts, key=lambda s: s.get("displayOrder", 999))]
    COLORS = {}
    for s in sorted(sts, key=lambda s: (s["id"] not in (1, 2, 3, 4), s.get("displayOrder", 999))):
        b = SMAP[str(s["id"])]
        if b not in COLORS and s.get("color"): COLORS[b] = s["color"]
    DONE = [s["id"] for s in sts if SMAP[str(s["id"])] == "Closed"]
    OPENIDS = [s["id"] for s in sts if s["id"] not in DONE]
    op = [slim(i) for i in paginate("issues", {"projectId[]": PID, "statusId[]": OPENIDS}, f"{slug}.open")]
    cl = [slim(i) for i in paginate("issues", {"projectId[]": PID, "statusId[]": DONE, "updatedSince": WIDE}, f"{slug}.closed")]
    seen, RAW = set(), []
    for i in op + cl:
        if i["key"] in seen: continue
        seen.add(i["key"]); RAW.append(i)
    pulls[slug] = {"RAW": RAW, "SMAP": SMAP, "pid": PID, "statuses": STATUSES, "colors": COLORS}
    print(f"{slug} ({key}): RAW={len(RAW)} open={len(op)} closed={len(cl)} statuses={len(sts)}")

os.makedirs(os.path.dirname(a.out), exist_ok=True)
out = {"generated": TODAY, "today": TODAY, "space": SPACE, "pulls": pulls}
json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
print(f"WROTE {a.out}  projects={len(pulls)} issues={sum(len(p['RAW']) for p in pulls.values())}")
