"""Oracle observer, paused execution controller and durable owner intent."""
from __future__ import annotations
import argparse
import contextlib
import importlib.metadata
import json
import logging
import os
import signal
import socket
import socketserver
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import dumps
from .dashboard import DashboardState, DashboardCollector, write_snapshot
from .market_check import SDK_VERSION, readonly_transport, safe_error
from .pc_control import collection_window
from .operations import backup_database
from .visual_report import DailyMail


class Runtime:
    def __init__(self,root):
        self.root=Path(root)
        self.state=DashboardState(root,background=True)
        self.stop=threading.Event();self.collector=None;self.next_auth=0;self.token_day=None
        self.boot_id=uuid.uuid4().hex;self.sequence=1;self.at=datetime.now(timezone.utc).isoformat();self.error=None
        self.mail=DailyMail(self.state.pnl_lines.store);self.next_mail=0
        self.mail_state={"status":"SCHEDULED_BY_1930_JST","provider_accepted":False,"inbox_verified":False}
        self.output=self.state.directory/"market-check-oracle-live.json"
        self.preparer=None
        self.executor=None
        from .impulse_feed import StreamingImpulse
        self.impulse=StreamingImpulse(self.root,self.state.pnl_lines.store)
        from .research_sync import SupabaseArchive
        self.research_archive=SupabaseArchive(self.state.pnl_lines.store)

    def read(self):
        view=self.state.read()
        from .oracle_link import revalidate
        revalidate(view,datetime.now(timezone.utc))
        from .premium_executor import observation
        from .dashboard import load_json
        execution = self.executor.public(observation(load_json(self.output,8_000_000))) if self.executor else {
            "code_implemented":True,"status":"BLOCKED","execution_enabled":False,"phase":"IDLE",
            "blockers":["ORACLE_EXECUTOR_CONNECTION_REQUIRED"]}
        view["runtime"]={"host":"ORACLE","boot_id":self.boot_id,"heartbeat_sequence":self.sequence,
            "heartbeat_at":self.at,"error":self.error,"orders_enabled":execution["execution_enabled"],"collector_host":"ORACLE"}
        view["execution_controller"]=execution
        view['premium_impulse']=self.impulse.public()
        view['research_archive']=self.research_archive.public()
        algo=view.get('control',{}).get('algo')
        if isinstance(algo,dict):
            algo['blockers']=list(dict.fromkeys([r for r in algo.get('blockers',[]) if r not in
                ('ORACLE_EXECUTOR_NOT_IMPLEMENTED_OR_VERIFIED','ORACLE_EXECUTOR_CONNECTION_REQUIRED')]+execution['blockers']))
            algo['execution_enabled']=execution['execution_enabled']
            algo['status']='ON_READY' if execution['execution_enabled'] else 'ON_BLOCKED' if algo.get('desired_enabled') else 'OFF'
            algo['stop_status']='PERSISTENT_GTT_IMPLEMENTED_ACTIVATION_UNVERIFIED'
            view['control']['blockers']=algo['blockers']
            view['control']['execution_enabled']=execution['execution_enabled']
        view["runtime"]["initializing"]=self.sequence==1
        view["daily_email"]=self.mail_state
        view["execution_preparation"]=self.state.pnl_lines.store.meta("premium-preparation",
            {"status":"WAIT","reason":"PREPARATION_NOT_STARTED","execution_enabled":False,"broker_writes":False})
        return view

    def command(self,value):
        if value=={"action":"read"}:return self.read()
        if isinstance(value,dict) and set(value)=={"action","enabled"} and value["action"]=="intent" and type(value["enabled"]) is bool:
            result=self.state.algo_set(value["enabled"])
            current=self.read().get('control',{}).get('algo')
            if current: result.update(current)
            return result
        if isinstance(value,dict) and set(value)=={"action","withdrawal"} and value["action"]=="record_withdrawal":
            try:
                return self.state.record_withdrawal(value["withdrawal"])
            except ValueError as exc:
                return {"status":"INVALID_WITHDRAWAL","reason":str(exc),"money_moved":False,"broker_writes":False}
        raise ValueError("fixed read, owner intent or accounting command only")

    def connect(self,now):
        from growwapi import GrowwAPI
        from ..brokers.groww_data import GrowwMarketData
        from .smart_read import SmartOrderReader
        if importlib.metadata.version("growwapi")!=SDK_VERSION:raise ValueError("SDK pin mismatch")
        token=GrowwAPI.get_access_token(api_key=os.environ["GROWW_OBSERVER_API_KEY"],secret=os.environ["GROWW_OBSERVER_API_SECRET"])
        market=GrowwMarketData(token)
        self.collector=DashboardCollector(market,background_history=True,journal=self.state.monitor.journal,
            smart_loader=SmartOrderReader(market.groww,market.limiter))
        self.collector.news_loader=None
        self.collector.account_loader=lambda:None
        from .execution_data import GrowwPreparation
        self.preparer=GrowwPreparation(market,self.state.monitor.journal)
        from . import premium_strategy
        from .contracts import identity
        from .execution_gate import ExecutionGate
        from .oracle_orders import GrowwOrderTransport, OracleOrderGateway
        from .oracle_protection import PersistentProtection
        from .premium_executor import PremiumExecutor
        cfg=premium_strategy.load(self.root)
        # Exact immutable source directory, never a mutable default branch pin.
        release=Path(__file__).resolve().parents[3].name
        gate=ExecutionGate(self.state.monitor.journal,self.root/'.trader-paused',mode=os.environ.get('EXECUTION_MODE','paper'),
            release=release,host='ORACLE',clock=lambda:datetime.now(timezone.utc),policy_hash=identity(cfg))
        transport=GrowwOrderTransport(market,gate);gateway=OracleOrderGateway(self.state.monitor.journal,transport,gate)
        protection=PersistentProtection(self.state.monitor.journal,transport,gateway)
        self.executor=PremiumExecutor(self.state.monitor.journal,gateway,protection,gate,cfg,clock=gate.clock)
        self.collector.before_ownership=self.executor.reconcile_before_collection
        self.token_day=now.date()

    def preparation_loop(self):
        # Analytical margin POSTs share the process's read-only HTTP guard. They
        # cannot place, cancel, modify or arm broker orders. Never stall collection.
        from . import premium_strategy
        from .dashboard import load_json
        while not self.stop.wait(60):
            preparer=self.preparer
            if preparer:
                try:
                    preparer.safe_check(load_json(self.output,8_000_000),premium_strategy.load(self.root))
                except Exception:
                    self.state.pnl_lines.store.set_meta("premium-preparation",{"status":"WAIT",
                        "reason":"PREPARATION_INPUT_UNAVAILABLE","at":datetime.now(timezone.utc).isoformat(),
                        "execution_enabled":False,"broker_writes":False,"selected":None})

    def execution_loop(self):
        from .dashboard import load_json
        from .premium_executor import observation
        while not self.stop.wait(5):
            executor=self.executor
            if executor:
                executor.tick(observation(load_json(self.output,8_000_000)),
                    self.state.pnl_lines.store.meta('premium-preparation',{}))

    def report_loop(self):
        while not self.stop.wait(30):
            try:
                now=datetime.now(timezone.utc)
                self.state.capital_ledger.accrue(now)
                self.mail_state=self.mail.tick(self.read(),now)
                from .pc_control import JST
                local=now.astimezone(JST);day=local.date().isoformat()
                if (local.hour,local.minute)>=(19,5) and not self.state.pnl_lines.store.meta("oracle-backup-"+day):
                    result=backup_database(self.state.pnl_lines.store.path,self.root/".agent-state/backups"/(day+".sqlite3"))
                    self.state.pnl_lines.store.set_meta("oracle-backup-"+day,result)
            except Exception as exc:self.mail_state={"status":"REPORT_"+type(exc).__name__,"provider_accepted":False,"inbox_verified":False}

    def run(self):
        self.research_archive.start()
        threading.Thread(target=self.report_loop,daemon=True).start()
        with open(os.devnull,"w") as muted,contextlib.redirect_stdout(muted),contextlib.redirect_stderr(muted):
            logging.disable(logging.CRITICAL)
            with readonly_transport([],history=True,dashboard=True,calculations=True,feed=True,timeout_seconds=5):
                self.impulse.start()
                threading.Thread(target=self.preparation_loop,daemon=True).start()
                threading.Thread(target=self.execution_loop,daemon=True).start()
                while not self.stop.is_set():
                    started=time.monotonic();now=datetime.now(timezone.utc)
                    try:
                        if collection_window(now) and now.timestamp()>=self.next_auth:
                            if self.collector is None or self.token_day!=now.date():
                                if self.collector:self.collector.close()
                                self.connect(now)
                            # Serialize an owned write with raw ownership reads;
                            # calculation/history workers remain independent.
                            with self.executor.lock:
                                self.executor.reconcile_before_collection()
                                value=self.collector.sample()
                            write_snapshot(self.output,value)
                            self.impulse.attach(self.collector.market)
                            if value.get("status")=="BLOCKED" or any(r.get("code")=="403" for r in value.get("probes",{}).values()):
                                raise ConnectionError("read unavailable")
                        # Collection already persists observations. Building full
                        # chart views here duplicates SSH-reader work and delays ticks.
                        if self.collector:
                            from .dashboard import load_json
                            self.state.monitor.tick(value,load_json(self.root/"config/owner_strategies.json",16384),datetime.now(timezone.utc))
                        self.error=None
                    except Exception as exc:
                        self.error=safe_error(exc)
                        self.next_auth=now.timestamp()+60
                        if self.collector:self.collector.close()
                        self.collector=None
                        self.preparer=None
                        self.executor=None
                        self.impulse.detach()
                        write_snapshot(self.output,{"finished_at":now.isoformat(),"status":"BLOCKED","failure":self.error,"order_capability":False})
                    self.sequence+=1;self.at=datetime.now(timezone.utc).isoformat()
                    self.stop.wait(max(0,5-(time.monotonic()-started)))


def serve(root):
    # One process owns collection/reconciliation and the execution state machine.
    # A second process cannot steal the socket or overwrite a basket's step.
    import fcntl
    lease=Path(root)/'.agent-state/premium-executor.lock'
    lease.parent.mkdir(parents=True,exist_ok=True)
    process_lock=lease.open('a')
    fcntl.flock(process_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    runtime=Runtime(root);path=Path(root)/"observer.sock"
    if path.exists():path.unlink()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(12)
            try:
                raw=self.rfile.readline(4097)
                if len(raw)>4096:raise ValueError("oversized command")
                command=json.loads(raw)
                if command=={'action':'watch_impulse'}:
                    while not runtime.stop.is_set():
                        result=runtime.impulse.next_view()
                        self.wfile.write(dumps(result).encode()+b'\n');self.wfile.flush()
                        time.sleep(.1)
                    return
                result=runtime.command(command)
            except Exception:result={"status":"OBSERVER_REQUEST_FAILED","order_capability":False}
            self.wfile.write(dumps(result).encode()+b"\n")
    class Server(socketserver.ThreadingUnixStreamServer):daemon_threads=True
    with Server(str(path),Handler) as server:
        os.chmod(path,0o600)
        threading.Thread(target=runtime.run,daemon=True).start()
        server.serve_forever(poll_interval=.5)


def client(root):
    raw=sys.stdin.read(4097)
    value=json.loads(raw)
    if len(raw)>4096:raise ValueError("oversized command")
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
        channel.settimeout(12);channel.connect(str(Path(root)/"observer.sock"))
        channel.sendall(dumps(value).encode()+b"\n")
        with channel.makefile("rb") as stream:
            if value=={'action':'watch_impulse'}:
                while True:
                    response=stream.readline(65537)
                    if not response:return
                    if len(response)>65536:raise ValueError('oversized impulse event')
                    print(response.decode().strip(),flush=True)
            else:response=stream.readline(8_000_001)
    if len(response)>8_000_000:raise ValueError("oversized response")
    print(response.decode().strip())


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("serve","client"));parser.add_argument("--root",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="client":client(args.root)
    else:serve(args.root)


if __name__=="__main__":
    try:main()
    except Exception as exc:
        print(dumps({"status":"ORACLE_OBSERVER_UNAVAILABLE","error_type":type(exc).__name__,"order_capability":False}))
        raise SystemExit(2) from None
