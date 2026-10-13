"""Background news, journal backup and visual-report transport. No trading actions."""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, time, timezone
from pathlib import Path

from .contracts import dumps, keys, stamp
from .news import parse_feed, request_bytes
from .operations import backup_database
from .pc_control import JST
from .session import SessionJournal

FEEDS=("https://rbi.org.in/pressreleases_rss.xml",
       "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms")


def collect_news(now, *, fetch=request_bytes):
    articles,feeds=[],[]
    for url in FEEDS:
        try:
            rows=parse_feed(fetch(url),url,now)
            articles.extend(rows[:30])
            feeds.append({"feed":url,"status":"FRESH" if rows else "MISSING_OR_STALE","count":len(rows)})
        except Exception as exc:
            feeds.append({"feed":url,"status":"UNAVAILABLE","error_type":type(exc).__name__})
    return {"format":"trusted-news-evidence-v1","assessed_at":now.isoformat(),"risk":"UNKNOWN",
            "assessment":"UNASSESSED_REQUIRES_ANALYST","feeds":feeds,
            "articles":sorted({r['id']:r for r in articles}.values(),key=lambda r:r['published_at'],reverse=True)[:30]}


def transport_settings(value):
    keys(value,{"format","host","user","identity_file","receiver_path","send_daily_email"})
    if (value['format']!='trading-oracle-report-v1' or type(value['send_daily_email']) is not bool
            or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}",value['host'])
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}",value['user'])
            or not re.fullmatch(r"/opt/growing-trader/releases/[a-f0-9]{40}/scripts/trading_report_receiver.py",value['receiver_path'])
            or not Path(value['identity_file']).is_absolute()):
        raise ValueError('reviewed Oracle report transport required')
    return value


def send_report(value, config, *, run=subprocess.run):
    transport_settings(config)
    # This is a single fixed reviewed receiver, not a general remote shell API.
    ssh=Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/OpenSSH/ssh.exe'
    args=[str(ssh),'-F','NUL','-T','-i',config['identity_file'],
        '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no',
        '-o','AddKeysToAgent=no','-o','IdentitiesOnly=yes','-o','IdentityAgent=none',
        '-o','PasswordAuthentication=no','-o','KbdInteractiveAuthentication=no',
        '-o','ClearAllForwardings=yes','-o','ControlMaster=no','-o','ConnectTimeout=15',
        '-o','LogLevel=ERROR',config['user']+'@'+config['host'],
        'sudo -n /usr/bin/python3 -I '+config['receiver_path']]
    result=run(args,input=dumps(value),text=True,capture_output=True,timeout=55,
        **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    if result.returncode or len(result.stdout)>4096:
        raise ValueError('report transport unavailable')
    response=json.loads(result.stdout)
    keys(response,{'status','provider_accepted','recipient_server_accepted','inbox_verified'})
    if (response['status'] not in ('ACCEPTED','RECIPIENT_SERVER_ACCEPTED','RETRY_PENDING','MAIL_NOT_CONFIGURED','REVIEW_REQUIRED')
            or any(type(response[k]) is not bool for k in response if k!='status')
            or response['inbox_verified'] is not False):
        raise ValueError('invalid delivery response')
    return response


class PcMaintenance:
    def __init__(self,root,store):
        self.root,self.store=Path(root),store
        self.sessions=SessionJournal(store)
        self.next_news=0

    def news_tick(self,now):
        if now.timestamp()<self.next_news: return
        self.next_news=now.timestamp()+120
        result=collect_news(now)
        path=self.root/'.agent-state/pc-news.json'
        temporary=path.with_suffix('.tmp')
        temporary.write_text(dumps(result),encoding='utf-8')
        os.replace(temporary,path)

    def tick(self,now, *, sender=send_report):
        local=now.astimezone(JST)
        if local.weekday()>=5: return {'status':'NON_WEEKDAY'}
        day=local.date().isoformat()
        if local.time()>=time(19,5) and not self.store.meta('backup-'+day):
            result=backup_database(self.store.path,self.root/'.agent-state/backups'/('pc-monitor-'+day+'.sqlite3'))
            self.store.set_meta('backup-'+day,result)
        # Mail uses its own outbox and never controls orders, P&L or the pause marker.
        if not time(19,15)<=local.time()<time(19,45): return {'status':'BEFORE_OR_AFTER_EOD_WINDOW'}
        path=self.root/'.agent-state/oracle-report.json'
        if not path.is_file(): return {'status':'REPORT_TRANSPORT_UNCONFIGURED'}
        config=transport_settings(json.loads(path.read_text(encoding='utf-8-sig')))
        if not config['send_daily_email']: return {'status':'DAILY_REPORT_DISABLED'}
        key='pc-mail-'+day
        state=self.store.meta(key,{})
        if state.get('provider_accepted'): return state
        if state.get('next_attempt',0)>now.timestamp(): return state
        latest=self.store.latest()
        if not latest or latest['portfolio']['accounting_day']!=day:
            return {'status':'NO_CURRENT_SESSION_PUBLICATION'}
        payload=self.store.meta('pc-mail-payload-'+day)
        if payload is None:
            payload={'format':'trading-pc-report-v1','day':day,'snapshot':latest,
                'coverage':self.sessions.coverage(day)}
            self.store.set_meta('pc-mail-payload-'+day,payload)
        try:
            result=sender(payload,config)
            result.update(at=now.isoformat(),attempts=state.get('attempts',0)+1)
        except Exception as exc:
            result={'status':'TRANSPORT_RETRY_PENDING','provider_accepted':False,'recipient_server_accepted':False,
                'inbox_verified':False,'error_type':type(exc).__name__,'attempts':state.get('attempts',0)+1}
        result['next_attempt']=now.timestamp()+min(1800,60*2**min(result['attempts']-1,5))
        self.store.set_meta(key,result)
        return result
