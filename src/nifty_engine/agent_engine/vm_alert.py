"""Single approved VM alert from stdin; provider acceptance only."""
import json
import sys
import html
from .contracts import identity
from .reporting import resend

def main():
    raw=sys.stdin.read(16385)
    if len(raw)>16384:raise ValueError("bounded mail input required")
    v=json.loads(raw)
    if set(v)!={"key","sender","recipient","status","incident","details"} or v["status"] not in ("VM_UNREACHABLE","WORKER_STUCK","INVALID_HEARTBEAT"):
        raise ValueError("fixed alert schema required")
    if v["recipient"]!="loganlogesh17@gmail.com":raise ValueError("approved recipient required")
    from .failure_alerts import diagnostics
    from .contracts import stamp
    details=diagnostics(v['details'])
    try: details['detected_at']=stamp(v['details']['detected_at']).isoformat()
    except (KeyError,TypeError,ValueError):pass
    payload={"from":v["sender"],"to":[v["recipient"]],"subject":"Options Trader · "+v['status'],
        "html":"<div style='font-family:Arial;padding:24px;background:#fff4ec'><h2>Oracle observer unavailable</h2><p>"+v["status"]+"</p><p>Incident "+html.escape(v['incident'])+"</p><pre>"+html.escape(json.dumps(details,indent=2))+"</pre><p>Current values unknown. The PC cannot confirm broker or position state; inspect Groww.</p><p>Codex incident checks are scheduled, not proof of a completed wake-up.</p></div>"}
    resend(payload,v["key"],identity([v["incident"],"vm-alert-v1"]))
    print('{"status":"ACCEPTED","inbox_verified":false}')

if __name__=="__main__":
    try:main()
    except Exception:print('{"status":"ALERT_FAILED","inbox_verified":false}')
