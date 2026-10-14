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
    accounting = next((kind for kind in ("withdrawal", "investment") if isinstance(command,dict)
        and set(command)=={"action",kind} and command["action"]=="record_"+kind), None)
    strategy_switch = isinstance(command,dict) and set(command)=={'action','switch'} and command['action']=='strategy_switch'
    if accounting:
        from .capital import cashflow
        cashflow(command[accounting],datetime.now(timezone.utc),accounting)
    elif strategy_switch:
        from .strategy_controls import validate_command
        validate_command(command['switch'])
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


def notify(root, status, incident_id, details=None):
    """Independent Windows mail key is DPAPI protected; values never leave this pipe."""
    helper=Path(root)/"scripts/Send-TradingAlert.ps1"
    if os.name!="nt" or not helper.is_file():return "ALERT_TRANSPORT_UNAVAILABLE"
    try:
        result=subprocess.run(["powershell.exe","-NoProfile","-File",str(helper),"-Status",status,"-Incident",incident_id],
            input=dumps(details or {}),capture_output=True,text=True,timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(result.stdout).get("status","ALERT_FAILED")
    except (OSError,subprocess.TimeoutExpired,ValueError,TypeError):return "ALERT_FAILED"


class RemoteViewer:
    def __init__(self,root,store):
        self.root,self.store=Path(root),store
        self.config=settings(json.loads((self.root/".agent-state/oracle-viewer.json").read_text(encoding="utf-8-sig")))
        self.cache=None; self.lock=threading.Lock(); self.previous=None; self.progress_at=None
        self.status="CONNECTING"; self.failed_since=None; self.last_attempt=0
        saved=self.store.meta("vm-incident",{})
        self.incident=saved.get("id") if saved.get("status") not in (None,"RECOVERED") else None
        self.alert_status=saved.get("alert_status") if self.incident else None
        self.incident_details=saved.get('details',{})
        self.incident_code=saved.get('alert_code',saved.get('status'))
        self.bad_reads=0;self.healthy_since=None
        self.impulse=None;self.impulse_at=None
        self.failure_details={}

    def impulse_read(self):
        with self.lock:
            if self.impulse is None or time.monotonic()-(self.impulse_at or 0)>4:
                return {'format':'trading-impulse-v1','transport_status':'PC_STREAM_UNAVAILABLE','indices':{},'broker_writes':False}
            return copy.deepcopy(self.impulse)

    def impulse_watch(self,stop):
        config=settings(self.config)
        ssh=Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/OpenSSH/ssh.exe'
        args=[str(ssh),'-F','NUL','-T','-i',config['identity_file'],'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
              '-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','PasswordAuthentication=no','-o','KbdInteractiveAuthentication=no',
              '-o','ClearAllForwardings=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',
              config['user']+'@'+config['host'],'sudo -n -u trading-observer '+config['python']+' -I -m nifty_engine.agent_engine.oracle_runtime client --root '+config['root']]
        while not stop.is_set():
            process=None
            try:
                process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,
                    **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
                process.stdin.write('{"action":"watch_impulse"}\n');process.stdin.close()
                while not stop.is_set():
                    line=process.stdout.readline(65537)
                    if not line or len(line)>65536:break
                    value=json.loads(line)
                    if value.get('format')!='trading-impulse-v1' or value.get('broker_writes') is not False:break
                    with self.lock:self.impulse=value;self.impulse_at=time.monotonic()
            except Exception:pass
            finally:
                if process:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill()
                with self.lock:self.impulse_at=None
            stop.wait(5)

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
            self.failure_details={k:view.get('runtime',{}).get(k) for k in ('heartbeat_at','heartbeat_sequence')}
        except Exception as exc:
            if not fixed_clock:now=datetime.now(timezone.utc)
            self.failed_since=self.failed_since or now.timestamp()
            state="VM_UNREACHABLE" if now.timestamp()-self.failed_since>=30 else "RECONNECTING"
            self.failure_details={'stage':'SSH_OBSERVER_READ','error_type':type(exc).__name__}
        if state in ("VM_UNREACHABLE","WORKER_STUCK","INVALID_HEARTBEAT"):
            self.healthy_since=None
            self.bad_reads+=1
            if self.bad_reads<3:
                self.status=state
                return state
            if self.incident is None:
                self.incident="oracle-"+str(int(now.timestamp()))
                self.last_attempt=0
                from .failure_alerts import diagnostics
                self.incident_details=dict(diagnostics(self.failure_details),detected_at=now.isoformat())
                self.incident_code=state
            if now.timestamp()-self.last_attempt>=300 and self.alert_status not in ('ACCEPTED','DELIVERY_UNKNOWN'):
                self.last_attempt=now.timestamp()
                # Freeze context so a retry keeps the provider idempotency payload.
                if now.timestamp()-int(self.incident.split('-')[1])>=23*3600:
                    self.alert_status='DELIVERY_UNKNOWN'
                else:
                    try:self.alert_status=alert(self.root,self.incident_code,self.incident,self.incident_details)
                    except Exception:self.alert_status='ALERT_FAILED'
            self.store.set_meta("vm-incident",{"id":self.incident,"status":state,"at":now.isoformat(),
                "details":self.incident_details,"alert_code":self.incident_code,
                "alert_status":self.alert_status,"inbox_verified":False})
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
            # A market/position snapshot older than 15 seconds does not make
            # independently timestamped Groww funds older than their 45-second
            # limit. revalidate() above already blanks expired money. A failed
            # Oracle heartbeat still blanks money regardless of its age.
            if self.status!="HEALTHY" and "funds" in view:
                for k in list(view["funds"]):
                    if k.endswith("_inr"):view["funds"][k]=None
                view["funds"]["status"]="STALE"
            algo=view.get("control",{}).get("algo")
            if algo:
                algo["execution_enabled"]=False
                algo["blockers"]=list(dict.fromkeys(algo.get("blockers",[])+["VM_UNHEALTHY_OR_STALE"]))
        return view

    def prepare_owner_live(self):
        """Only an owner-clicked Algo On may invoke the fixed privileged setup.

        Never called by polling, refresh, research, schedules or background work.
        The root command only reads the broker and changes local mode/pause; it
        does not place orders, invent stop-fill proof or set Algo On.
        """
        config=settings(self.config)
        ssh=Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/OpenSSH/ssh.exe'
        fixed=[str(ssh),'-F','NUL','-T','-i',config['identity_file'],
            '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','UpdateHostKeys=no','-o','IdentitiesOnly=yes',
            '-o','IdentityAgent=none','-o','PasswordAuthentication=no',
            '-o','KbdInteractiveAuthentication=no','-o','ClearAllForwardings=yes',
            '-o','ConnectTimeout=8','-o','LogLevel=ERROR',config['user']+'@'+config['host']]
        def execute(action,digest=None):
            command='sudo -n '+config['python']+' -I -m nifty_engine.agent_engine.owner_setup '+action
            if digest:
                if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('INVALID_LIVE_CONFIRM_DIGEST')
                command+=' --confirm '+digest
            result=subprocess.run(fixed+[command],input='',capture_output=True,text=True,
                timeout=240,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            if len(result.stdout)>4096:raise ConnectionError('LIVE_SETUP_RESPONSE_TOO_LARGE')
            try:payload=json.loads(result.stdout)
            except (ValueError,TypeError):raise ConnectionError('LIVE_SETUP_RESPONSE_UNAVAILABLE') from None
            if (result.returncode or not isinstance(payload,dict)
                    or not isinstance(payload.get('status'),str)):
                return dict(status='OWNER_LIVE_SETUP_BLOCKED',
                            reason=payload.get('reason','REVIEW_FAILED') if isinstance(payload,dict) else 'REVIEW_FAILED')
            return payload
        preview=execute('live-preview')
        if preview.get('status')!='LIVE_STRATEGY_REVIEW_READY':
            return dict(status='OWNER_LIVE_SETUP_BLOCKED',reason=preview.get('reason','REVIEW_UNAVAILABLE'))
        digest=preview.get('evidence_digest')
        if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
            return dict(status='OWNER_LIVE_SETUP_BLOCKED',reason='REVIEW_DIGEST_INVALID')
        result=execute('live-enable',digest)
        if result.get('status')!='LIVE_PREPARED_ALGO_OFF':
            return dict(status='OWNER_LIVE_SETUP_BLOCKED',reason=result.get('reason','LIVE_RESTART_UNCONFIRMED'))
        # Retry READ ONLY while the owner-authorized service restart settles.
        for _ in range(6):
            try:
                view=request(config,{'action':'read'})
                setup=view.get('live_setup',{})
                if (setup.get('mode')=='live' and setup.get('paused') is False
                    # The account identity remains unread until the next
                    # broker collection window. Root already confirmed the
                    # immutable owner receipt and live state before returning.
                    and view.get('control',{}).get('algo',{}).get('desired_enabled') is False):
                    return dict(status='LIVE_PREPARED_ALGO_OFF',broker_writes=False)
            except (ConnectionError,OSError,ValueError,subprocess.TimeoutExpired):
                pass
            time.sleep(2)
        return dict(status='OWNER_LIVE_SETUP_BLOCKED',reason='LIVE_RUNTIME_NOT_CONFIRMED')

    def set_intent(self,enabled):
        if type(enabled) is not bool:raise ValueError("explicit owner boolean required")
        result=request(self.config,{"action":"intent","enabled":enabled})
        intent=result.get('owner_intent') if isinstance(result,dict) else None
        if (not isinstance(result,dict) or result.get('desired_enabled') is not enabled
                or not isinstance(intent,dict) or intent.get('enabled') is not enabled
                or result.get('status') not in (('ON_BLOCKED','ON_READY') if enabled else ('OFF',))):
            raise ConnectionError('Oracle did not confirm the owner setting')
        self.poll()
        return result

    def strategy_switch(self,body):
        result=request(self.config,{'action':'strategy_switch','switch':body})
        if result.get('status') not in ('SAVED','UNCHANGED','REVISION_CONFLICT') or result.get('broker_writes') is not False:
            raise ConnectionError('Strategy preference was not confirmed')
        self.poll()
        return result

    def record_withdrawal(self,body):
        return self._record_capital(body,"withdrawal")

    def record_investment(self,body):
        return self._record_capital(body,"investment")

    def _record_capital(self,body,kind):
        result=request(self.config,{"action":"record_"+kind,kind:body})
        if result.get("status")=="INVALID_"+kind.upper():
            raise ValueError(result.get("reason","INVALID_"+kind.upper()))
        if result.get("record_id") != body["id"] or not isinstance(result.get("capital_summary"),dict) or result.get("status") not in ("RECORDED","ALREADY_RECORDED") or result.get("money_moved") is not False or result.get("broker_writes") is not False:
            raise ConnectionError("Accounting response unavailable")
        with self.lock:
            if self.cache is not None:self.cache["capital_summary"]=result["capital_summary"]
        return result

    def run(self,stop):
        threading.Thread(target=self.impulse_watch,args=(stop,),daemon=True).start()
        while not stop.is_set():
            start=time.monotonic()
            self.poll()
            stop.wait(max(0,5-(time.monotonic()-start)))
