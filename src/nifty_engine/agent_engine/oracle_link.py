"""Private SSH viewer/control transport and independent PC outage detection."""
from __future__ import annotations
import copy
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .contracts import dumps, keys, stamp


def settings(value):
    keys(value,{"format","host","user","identity_file","python","root"})
    if (value["format"]!="trading-oracle-viewer-v1"
        or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}",value["host"])
        or value["user"]!="ubuntu" or not Path(value["identity_file"]).is_absolute()
        or not re.fullmatch(r"/opt/growing-trader/releases/[a-f0-9]{40}/venv/bin/python",value["python"])
        or value["root"]!="/var/lib/trading-observer"):
        raise ValueError("reviewed private Oracle viewer settings required")
    return value


def request(config, command, *, run=subprocess.run):
    settings(config)
    accounting = isinstance(command,dict) and set(command)=={"action","withdrawal"} and command["action"]=="record_withdrawal"
    if accounting:
        from .capital import withdrawal
        withdrawal(command["withdrawal"],datetime.now(timezone.utc))
    elif command not in ({"action":"read"},{"action":"intent","enabled":True},{"action":"intent","enabled":False}):
        raise ValueError("fixed read, intent or accounting command required")
    ssh=Path(os.environ.get("SYSTEMROOT","C:/Windows"))/"System32/OpenSSH/ssh.exe"
    args=[str(ssh),"-F","NUL","-T","-i",config["identity_file"],"-o","BatchMode=yes",
        "-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","IdentitiesOnly=yes",
        "-o","IdentityAgent=none","-o","PasswordAuthentication=no","-o","KbdInteractiveAuthentication=no",
        "-o","ClearAllForwardings=yes","-o","ConnectTimeout=8","-o","LogLevel=ERROR",
        config["user"]+"@"+config["host"],
        "sudo -n -u trading-observer "+config["python"]+" -I -m nifty_engine.agent_engine.oracle_runtime client --root "+config["root"]]
    response=run(args,input=dumps(command),capture_output=True,text=True,timeout=18,
        **({"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}))
    if response.returncode or len(response.stdout)>8_000_000:
        raise ConnectionError("Oracle observer unavailable")
    value=json.loads(response.stdout)
    if command["action"]=="read" and (value.get("format")!="trading-dashboard-v1" or value.get("order_capability") is not False):
        raise ValueError("read-only dashboard response required")
    return value


def health(runtime, now, *, previous=None, progress_at=None):
    """A reachable host is not proof of a progressing worker."""
    try:
        age=(now-stamp(runtime["heartbeat_at"])).total_seconds()
        identity=(runtime["boot_id"],runtime["heartbeat_sequence"])
        if type(identity[1]) is not int or identity[1]<1 or not isinstance(identity[0],str):raise ValueError()
    except (ValueError,TypeError,KeyError):
        return "INVALID_HEARTBEAT",previous,progress_at
    changed=identity!=previous
    progress_at=now.timestamp() if changed or progress_at is None else progress_at
    if runtime.get("initializing") is True and identity[1]==1 and 0<=age<=120:
        return "STARTING",identity,progress_at
    # Host clocks can differ slightly. This tolerance applies to heartbeat
    # liveness only; quote, funds, P&L and news freshness remain strict.
    state="WORKER_STUCK" if not -5<=age<=30 or now.timestamp()-progress_at>30 else "HEALTHY"
    return state,identity,progress_at


def revalidate(view,now):
    """Every response rechecks each provider clock, including cached VM views."""
    from .broker_pnl import public
    from .capital import revalidate as capital_revalidate
    view["capital_summary"]=capital_revalidate(view.get("capital_summary"),now)
    series=view.get("broker_pnl",{}).get("series",[])
    view["broker_pnl"]=dict(public(view.get("broker_pnl"),now),series=series)
    def current(at,limit):
        try:return 0<=(now-stamp(at)).total_seconds()<=limit
        except (ValueError,TypeError):return False
    funds=view.get("funds",{})
    if not current(funds.get("received_at"),45):
        funds["status"]="STALE"
        for k in funds:
            if k.endswith("_inr"):funds[k]=None
    for market in view.get("markets",[]):
        if not current(market.get("received_at"),15):
            for k in ("price","change","change_pct","high","low"):market[k]=None
        for option in market.get("options",[]):
            if not current(option.get("received_at"),15):
                for k in ("last_price","bid","ask","open_pnl_inr"):option[k]=None
            if view["broker_pnl"]["status"]!="AVAILABLE":option["open_pnl_inr"]=None
    return view


def notify(root, status, incident_id):
    """Independent Windows mail key is DPAPI protected; values never leave this pipe."""
    helper=Path(root)/"scripts/Send-TradingAlert.ps1"
    if os.name!="nt" or not helper.is_file():return "ALERT_TRANSPORT_UNAVAILABLE"
    result=subprocess.run(["powershell.exe","-NoProfile","-File",str(helper),"-Status",status,"-Incident",incident_id],
        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
    try:return json.loads(result.stdout).get("status","ALERT_FAILED")
    except (ValueError,TypeError):return "ALERT_FAILED"


class RemoteViewer:
    def __init__(self,root,store):
        self.root,self.store=Path(root),store
        self.config=settings(json.loads((self.root/".agent-state/oracle-viewer.json").read_text(encoding="utf-8-sig")))
        self.cache=None; self.lock=threading.Lock(); self.previous=None; self.progress_at=None
        self.status="CONNECTING"; self.failed_since=None; self.last_attempt=0
        saved=self.store.meta("vm-incident",{})
        self.incident=saved.get("id") if saved.get("status") not in (None,"RECOVERED") else None
        self.alert_status=saved.get("alert_status") if self.incident else None
        self.bad_reads=0;self.healthy_since=None

    def poll(self, now=None, *, fetch=request, alert=notify):
        fixed_clock=now is not None
        now=now or datetime.now(timezone.utc)
        try:
            view=fetch(self.config,{"action":"read"})
            if not fixed_clock:now=datetime.now(timezone.utc)
            state,self.previous,self.progress_at=health(view.get("runtime",{}),now,
                previous=self.previous,progress_at=self.progress_at)
            with self.lock:self.cache=view
            self.failed_since=None
        except Exception:
            if not fixed_clock:now=datetime.now(timezone.utc)
            self.failed_since=self.failed_since or now.timestamp()
            state="VM_UNREACHABLE" if now.timestamp()-self.failed_since>=30 else "RECONNECTING"
        if state in ("VM_UNREACHABLE","WORKER_STUCK","INVALID_HEARTBEAT"):
            self.healthy_since=None
            self.bad_reads+=1
            if self.bad_reads<3:
                self.status=state
                return state
            if self.incident is None:
                self.incident="oracle-"+str(int(now.timestamp()))
                self.last_attempt=0
            if now.timestamp()-self.last_attempt>=300 and self.alert_status!="ACCEPTED":
                self.last_attempt=now.timestamp()
                self.alert_status=alert(self.root,state,self.incident)
            self.store.set_meta("vm-incident",{"id":self.incident,"status":state,"at":now.isoformat(),"alert_status":self.alert_status})
        elif state=="HEALTHY":
            self.bad_reads=0
            self.healthy_since=self.healthy_since or now.timestamp()
            if self.incident and now.timestamp()-self.healthy_since>=60:
                self.store.set_meta("vm-recovery",{"id":self.incident,"at":now.isoformat()})
                self.store.set_meta("vm-incident",{"id":self.incident,"status":"RECOVERED","at":now.isoformat()})
                self.incident=None; self.alert_status=None
        else:
            self.bad_reads=0;self.healthy_since=None
        self.status=state
        return state

    def read(self):
        from .dashboard import view_model, load_json
        now=datetime.now(timezone.utc)
        with self.lock:view=copy.deepcopy(self.cache)
        if view is None:view=view_model(None,load_json(self.root/"config/owner_strategies.json"),now=now)
        revalidate(view,now)
        at=view.get("as_of")
        fresh=bool(at and 0<=(now-stamp(at)).total_seconds()<=15 and self.status=="HEALTHY")
        view.update(source="Oracle VM · Groww read-only",background_monitor=True,offline=False,
            freshness="RECENT" if fresh else "STALE",refreshing=self.status=="HEALTHY",refresh_error=None)
        view["vm"]={"status":self.status,"alert_status":self.alert_status,"monitor_host":"PC",
            "requires_pc_awake":True,"incident":bool(self.incident)}
        if self.status!="HEALTHY":
            from .capital import revalidate as capital_revalidate
            capital_revalidate(view["capital_summary"],now,cached=True)
        if not fresh:
            from .broker_pnl import empty
            series=view.get("broker_pnl",{}).get("series",[])
            view["broker_pnl"]=dict(empty("STALE",at),series=series)
            for market in view["markets"]:
                for k in ("price","change","change_pct","high","low"):market[k]=None
                for option in market["options"]:
                    for k in ("last_price","bid","ask","open_pnl_inr"):option[k]=None
            for k in list(view.get("funds",{})):
                if k.endswith("_inr"):view["funds"][k]=None
            if "funds" in view:view["funds"]["status"]="STALE"
            algo=view.get("control",{}).get("algo")
            if algo:
                algo["execution_enabled"]=False
                algo["blockers"]=list(dict.fromkeys(algo.get("blockers",[])+["VM_UNHEALTHY_OR_STALE"]))
        return view

    def set_intent(self,enabled):
        if type(enabled) is not bool:raise ValueError("explicit owner boolean required")
        result=request(self.config,{"action":"intent","enabled":enabled})
        self.poll()
        return result

    def record_withdrawal(self,body):
        result=request(self.config,{"action":"record_withdrawal","withdrawal":body})
        if result.get("status")=="INVALID_WITHDRAWAL":
            raise ValueError(result.get("reason","INVALID_WITHDRAWAL"))
        if result.get("status") not in ("RECORDED","ALREADY_RECORDED") or result.get("money_moved") is not False or result.get("broker_writes") is not False:
            raise ConnectionError("Accounting response unavailable")
        with self.lock:
            if self.cache is not None:self.cache["capital_summary"]=result["capital_summary"]
        return result

    def run(self,stop):
        while not stop.is_set():
            start=time.monotonic()
            self.poll()
            stop.wait(max(0,5-(time.monotonic()-start)))
