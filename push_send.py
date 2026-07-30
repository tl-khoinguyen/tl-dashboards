#!/usr/bin/env python3
"""Send a Web Push "dashboard updated" ping to every stored subscription.
Runs in CI (notify job) after a successful scheduled deploy — never on manual
dev builds unless the dispatch sets notify=true. Env:
  PUSH_WORKER_URL   subscription registry (Cloudflare worker)
  PUSH_LIST_TOKEN   bearer token for /list and /gone
  VAPID_PRIVATE     base64url P-256 private key
  SITE_URL          optional — notification click target base
Payload is minimal (title/body/url) — no dashboard data travels through push.
Dead subscriptions (404/410 from the push service) are pruned via /gone.
Note: the worker sits behind Cloudflare, which 403s blank/default UAs — always
send a User-Agent."""
import os, json, sys, datetime, urllib.request
from pywebpush import webpush, WebPushException
sys.stdout.reconfigure(encoding="utf-8")

URL = os.environ["PUSH_WORKER_URL"].rstrip("/")
UA = {"User-Agent": "tlk-ci/1.0"}

def call(path, method="GET", data=None):
    req = urllib.request.Request(URL + path, method=method,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json", **UA,
                 "Authorization": "Bearer " + os.environ["PUSH_LIST_TOKEN"]})
    return json.load(urllib.request.urlopen(req))

tz = datetime.timezone(datetime.timedelta(hours=float(os.environ.get("TZ_OFFSET_HOURS", "7"))))
now = datetime.datetime.now(tz)
edition = "朝の更新" if now.hour < 10 else "昼の更新"   # 2x daily: ~05:30 / ~13:00 VN
site = os.environ.get("SITE_URL", "").rstrip("/")
payload = json.dumps({"title": "TL Dashboards",
                      "body": f"{now:%Y-%m-%d %H:%M} {edition} / Dữ liệu mới",
                      "url": (site + "/all/") if site else None}, ensure_ascii=False)

subs = call("/list")["subs"]
ok = dead = err = 0
for s in subs:
    try:
        # ttl matters: the default is 0 = "deliver instantly or DROP" — a phone
        # in Doze at 05:42 silently loses the ping (bit us 07.30). 24h TTL keeps
        # it queued until the device wakes; Urgency high may wake it sooner.
        webpush(s, payload, vapid_private_key=os.environ["VAPID_PRIVATE"],
                vapid_claims={"sub": "mailto:webpush@thankslab.biz"},
                ttl=86400, headers={"Urgency": "high"})
        ok += 1
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (404, 410):
            call("/gone", "DELETE", {"endpoint": s["endpoint"]}); dead += 1
        else:
            err += 1; print(f"push error {code}: {e}", file=sys.stderr)
print(f"push sent={ok} pruned={dead} errors={err} of {len(subs)}")
