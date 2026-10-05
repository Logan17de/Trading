"""Single approved VM alert from stdin; provider acceptance only."""
import json
import sys
from .contracts import identity
from .reporting import resend

def main():
    raw=sys.stdin.read(16385)
    if len(raw)>16384:raise ValueError("bounded mail input required")
    v=json.loads(raw)
    if set(v)!={"key","sender","recipient","status","incident"} or v["status"] not in ("VM_UNREACHABLE","WORKER_STUCK","INVALID_HEARTBEAT"):
        raise ValueError("fixed alert schema required")
    if v["recipient"]!="loganlogesh17@gmail.com":raise ValueError("approved recipient required")
    payload={"from":v["sender"],"to":[v["recipient"]],"subject":"Options Trader · VM needs attention",
        "html":"<div style='font-family:Arial;padding:24px;background:#fff4ec'><h2>Oracle observer unavailable</h2><p>"+v["status"]+"</p><p>Current values unknown · Trading blocked</p></div>"}
    resend(payload,v["key"],identity([v["incident"],"vm-alert-v1"]))
    print('{"status":"ACCEPTED","inbox_verified":false}')

if __name__=="__main__":
    try:main()
    except Exception:print('{"status":"ALERT_FAILED","inbox_verified":false}')
