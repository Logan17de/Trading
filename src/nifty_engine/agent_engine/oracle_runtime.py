"""Oracle observer and durable owner intent. No order executor or public port."""
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

    def read(self):
        view=self.state.read()
        from .oracle_link import revalidate
        revalidate(view,datetime.now(timezone.utc))
        view["runtime"]={"host":"ORACLE","boot_id":self.boot_id,"heartbeat_sequence":self.sequence,
            "heartbeat_at":self.at,"error":self.error,"orders_enabled":False,"collector_host":"ORACLE"}
        view["runtime"]["initializing"]=self.sequence==1
        view["daily_email"]=self.mail_state
        return view

    def command(self,value):
        if value=={"action":"read"}:return self.read()
        if isinstance(value,dict) and set(value)=={"action","enabled"} and value["action"]=="intent" and type(value["enabled"]) is bool:
            return self.state.algo_set(value["enabled"])
        raise ValueError("read or explicit owner intent only")

    def connect(self,now):
        from growwapi import GrowwAPI
        from ..brokers.groww_data import GrowwMarketData
        from .smart_read import SmartOrderReader
        if importlib.metadata.version("growwapi")!=SDK_VERSION:raise ValueError("SDK pin mismatch")
        token=GrowwAPI.get_access_token(api_key=os.environ["GROWW_OBSERVER_API_KEY"],secret=os.environ["GROWW_OBSERVER_API_SECRET"])
        market=GrowwMarketData(token)
        self.collector=DashboardCollector(market,background_history=True,journal=self.state.monitor.journal,
            smart_loader=SmartOrderReader(market.groww,market.limiter))
        self.collector.news_loader=lambda:self.state.monitor.news.snapshot(datetime.now(timezone.utc))
        self.collector.account_loader=lambda:None
        self.token_day=now.date()

    def report_loop(self):
        while not self.stop.wait(30):
            try:
                now=datetime.now(timezone.utc)
                self.mail_state=self.mail.tick(self.read(),now)
                from .pc_control import JST
                local=now.astimezone(JST);day=local.date().isoformat()
                if (local.hour,local.minute)>=(19,5) and not self.state.pnl_lines.store.meta("oracle-backup-"+day):
                    result=backup_database(self.state.pnl_lines.store.path,self.root/".agent-state/backups"/(day+".sqlite3"))
                    self.state.pnl_lines.store.set_meta("oracle-backup-"+day,result)
            except Exception as exc:self.mail_state={"status":"REPORT_"+type(exc).__name__,"provider_accepted":False,"inbox_verified":False}

    def run(self):
        threading.Thread(target=self.state.monitor.news.run,args=(self.stop,),daemon=True).start()
        threading.Thread(target=self.report_loop,daemon=True).start()
        with open(os.devnull,"w") as muted,contextlib.redirect_stdout(muted),contextlib.redirect_stderr(muted):
            logging.disable(logging.CRITICAL)
            with readonly_transport([],history=True,dashboard=True,timeout_seconds=5):
                while not self.stop.is_set():
                    started=time.monotonic();now=datetime.now(timezone.utc)
                    try:
                        if collection_window(now) and now.timestamp()>=self.next_auth:
                            if self.collector is None or self.token_day!=now.date():
                                if self.collector:self.collector.close()
                                self.connect(now)
                            value=self.collector.sample()
                            write_snapshot(self.output,value)
                            if value.get("status")=="BLOCKED" or any(r.get("code")=="403" for r in value.get("probes",{}).values()):
                                raise ConnectionError("read unavailable")
                        self.state.read()
                        self.error=None
                    except Exception as exc:
                        self.error=safe_error(exc)
                        self.next_auth=now.timestamp()+60
                        if self.collector:self.collector.close()
                        self.collector=None
                        write_snapshot(self.output,{"finished_at":now.isoformat(),"status":"BLOCKED","failure":self.error,"order_capability":False})
                    self.sequence+=1;self.at=datetime.now(timezone.utc).isoformat()
                    self.stop.wait(max(0,5-(time.monotonic()-started)))


def serve(root):
    runtime=Runtime(root);path=Path(root)/"observer.sock"
    if path.exists():path.unlink()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(12)
            try:
                raw=self.rfile.readline(4097)
                if len(raw)>4096:raise ValueError("oversized command")
                result=runtime.command(json.loads(raw))
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
        with channel.makefile("rb") as stream:response=stream.readline(8_000_001)
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
