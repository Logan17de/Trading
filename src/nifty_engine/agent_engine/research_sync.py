"""Durable, one-way Oracle research archive. No broker or execution dependencies."""
import base64
import gzip
import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime,timezone
from zoneinfo import ZoneInfo

PROJECT_URL='https://imirspxhbnerxknyynqx.supabase.co'
TABLE='trading_research_records'
IST=ZoneInfo('Asia/Kolkata')
META_KEYS=('premium-executor-v1','premium-executor-status','premium-preparation','report-strategy-evaluations')


def encoded(body):
    return json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(body):
    return hashlib.sha256(encoded(body).encode()).hexdigest()


def scrub(value):
    """Source categories are allowlisted; remove credentials, raw errors and identities."""
    if isinstance(value,dict):
        result={}
        for key,body in value.items():
            name=key.lower()
            if any(word in name for word in ('secret','token','credential','password','api_key','service_role','account_id','client_id','user_id','email')) or name in ('pan','pan_number','pin','error','raw','response','auth','url'):
                continue
            if name in ('broker_id','groww_order_id','smart_order_id'):
                result[key+'_sha256']=hashlib.sha256(str(body).encode()).hexdigest() if body is not None else None
            else:result[key]=scrub(body)
        return result
    if isinstance(value,list):return [scrub(v) for v in value]
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class SupabaseArchive:
    def __init__(self,store,*,url=None,key=None,post=None,clock=lambda:datetime.now(timezone.utc)):
        self.store=store;self.clock=clock
        self.url=(url if url is not None else os.environ.get('SUPABASE_URL','')).rstrip('/')
        self._key=key if key is not None else os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        if self.url and self.url!=PROJECT_URL:raise ValueError('REVIEWED_SUPABASE_PROJECT_REQUIRED')
        self.post=post or self._post
        self.stop=threading.Event();self.last_ack=None;self.status='WAITING_FOR_SERVER_CONFIGURATION'
        self.failure=None;self.uploaded=0;self.lock=threading.RLock();self.step_lock=threading.Lock()
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS research_sync_outbox(
                    record_key TEXT PRIMARY KEY,body TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS research_sync_seen(
                    category TEXT NOT NULL,record_key TEXT NOT NULL,hash TEXT NOT NULL,
                    PRIMARY KEY(category,record_key));
            ''')
            old=db.execute("SELECT body FROM meta WHERE key='research-sync-source-id'").fetchone()
            self.source_id=json.loads(old[0]) if old else str(uuid.uuid4())
            if not old:db.execute("INSERT INTO meta VALUES('research-sync-source-id',?)",(json.dumps(self.source_id),))

    def _post(self,rows):
        if self.url!=PROJECT_URL or not self._key:raise PermissionError('SERVER_CONFIGURATION_REQUIRED')
        data=encoded([dict(r,uploaded_at=self.clock().isoformat()) for r in rows]).encode()
        if len(data)>1_000_000:raise ValueError('ARCHIVE_BATCH_TOO_LARGE')
        headers={'apikey':self._key,'Content-Type':'application/json',
                 'Prefer':'resolution=merge-duplicates,return=minimal'}
        # Modern sb_secret keys use apikey; legacy service-role JWTs also need bearer.
        if self._key.count('.')==2:headers['Authorization']='Bearer '+self._key
        request=urllib.request.Request(self.url+'/rest/v1/'+TABLE+'?on_conflict=source_id,record_key',
                                       data=data,headers=headers,method='POST')
        with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response:
            if response.status not in (200,201,204):raise ConnectionError('ARCHIVE_NOT_ACCEPTED')
            response.read(1024)

    def _enqueue(self,db,category,key,day,body,index=None,at=None,*,append=False):
        body=scrub(body);sha=digest(body)
        previous=db.execute('SELECT hash FROM research_sync_seen WHERE category=? AND record_key=?',(category,key)).fetchone()
        if previous and previous[0]==sha:return
        cloud_key=category+':'+key+(':'+sha if append else '')
        row=dict(source_id=self.source_id,record_key=cloud_key,category=category,trade_date=day,
            index_name=index,observed_at=datetime.fromtimestamp(at or self.clock().timestamp(),timezone.utc).isoformat(),
            body=body,content_sha256=sha,mode='MONITOR_ONLY')
        db.execute('INSERT OR REPLACE INTO research_sync_outbox(record_key,body) VALUES(?,?)',(cloud_key,encoded(row)))
        db.execute('INSERT OR REPLACE INTO research_sync_seen VALUES(?,?,?)',(category,key,sha))

    def capture(self):
        """Capture and advance cursors atomically, before any network request."""
        now=self.clock();day=now.astimezone(IST).date().isoformat()
        with self.store.transaction() as db:
            if db.execute('SELECT count(*) FROM research_sync_outbox').fetchone()[0]>=5000:return
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'premium_impulse_ticks' in tables:
                cursor=db.execute("SELECT body FROM meta WHERE key='research-sync-tick-cursor'").fetchone()
                after=json.loads(cursor[0]) if cursor else 0
                rows=db.execute('SELECT * FROM premium_impulse_ticks WHERE id>? ORDER BY id LIMIT 1000',(after,)).fetchall()
                for offset in range(0,len(rows),100):
                    part=rows[offset:offset+100];raw=[dict(r,body=json.loads(r['body'])) for r in part]
                    payload=encoded(raw).encode()
                    packed=dict(encoding='gzip+base64+json',count=len(part),first_id=part[0]['id'],last_id=part[-1]['id'],
                                raw_sha256=hashlib.sha256(payload).hexdigest(),data=base64.b64encode(gzip.compress(payload,mtime=0)).decode())
                    configs={row['body'].get('research_config_sha256') for row in raw}-{None}
                    packed['research_configs']={sha:self.store.meta('premium-research-config-'+sha) for sha in configs}
                    self._enqueue(db,'impulse_ticks',str(part[0]['id'])+'-'+str(part[-1]['id']),part[0]['trade_date'],packed,at=part[0]['receive_timestamp'])
                if rows:db.execute("INSERT OR REPLACE INTO meta VALUES('research-sync-tick-cursor',?)",(json.dumps(rows[-1]['id']),))
            if 'premium_impulse_events' in tables:
                for r in db.execute('SELECT * FROM premium_impulse_events').fetchall():
                    self._enqueue(db,'impulse_event',r['event_id'],r['trade_date'],json.loads(r['body']),r['index_name'],r['start'])
                for r in db.execute('SELECT o.*,e.trade_date,e.index_name,e.start FROM premium_impulse_outcomes o JOIN premium_impulse_events e USING(event_id)').fetchall():
                    self._enqueue(db,'impulse_outcome',r['event_id']+':'+str(r['horizon']),r['trade_date'],json.loads(r['body']),r['index_name'],r['start'])
                for r in db.execute('SELECT * FROM daily_research_summary').fetchall():
                    self._enqueue(db,'daily_summary',r['trade_date'],r['trade_date'],json.loads(r['body']),at=r['generated_at'])
            if 'pc_orders' in tables:
                for r in db.execute('SELECT o.*,s.strategy FROM pc_orders o JOIN pc_slots s USING(slot)').fetchall():
                    body=dict(r)
                    if body['strategy'] not in ('EVERYDAY','LATE_SESSION'):continue
                    self._enqueue(db,'algo_order',r['reference'],day,body,append=True)
            placeholders=','.join('?' for _ in META_KEYS)
            for r in db.execute("SELECT * FROM meta WHERE key IN ("+placeholders+") OR key LIKE 'prepared-order-%'",META_KEYS).fetchall():
                self._enqueue(db,'algo_state',r['key'],day,json.loads(r['body']),append=True)
            if 'pc_pnl_observations' in tables:
                cursor=db.execute("SELECT body FROM meta WHERE key='research-sync-pnl-cursor'").fetchone()
                after=json.loads(cursor[0]) if cursor else 0
                rows=db.execute('SELECT * FROM pc_pnl_observations WHERE at>? ORDER BY at LIMIT 2000',(after,)).fetchall()
                for r in rows:
                    raw=json.loads(r['body']);body=dict(status=raw.get('status'),basis=raw.get('basis'),
                        received_at=raw.get('received_at'),observed_at=raw.get('observed_at'),algo=raw.get('buckets',{}).get('algo'))
                    self._enqueue(db,'algo_pnl',str(r['at']),r['day'],body,at=r['at'])
                if rows:db.execute("INSERT OR REPLACE INTO meta VALUES('research-sync-pnl-cursor',?)",(json.dumps(rows[-1]['at']),))

    def step(self):
        with self.step_lock:
            self.capture()
            if self.url!=PROJECT_URL or not self._key:
                self.status='WAITING_FOR_SERVER_CONFIGURATION';return
            now=self.clock().timestamp()
            rows=self.store.read('SELECT * FROM research_sync_outbox WHERE next_attempt<=? ORDER BY rowid LIMIT 50',(now,))
            if not rows:self.status='SYNCED' if not self.pending() else 'RETRY_WAIT';return
            body=[];selected=[];size=2
            for r in rows:
                if size+len(r['body'].encode())>900_000:break
                size+=len(r['body'].encode())+1;body.append(json.loads(r['body']));selected.append(r)
            if not selected:raise ValueError('ARCHIVE_RECORD_TOO_LARGE')
            try:self.post(body)
            except Exception as exc:
                code=str(getattr(exc,'code',''))
                self.failure='SUPABASE_HTTP_'+code if re.fullmatch(r'[0-9]{3}',code) else 'SUPABASE_UPLOAD_UNAVAILABLE'
                self.status='RETRY_PENDING'
                with self.store.transaction() as db:
                    for r in selected:db.execute('UPDATE research_sync_outbox SET attempts=attempts+1,next_attempt=? WHERE record_key=?',
                        (now+min(300,5*2**min(r['attempts'],6)),r['record_key']))
                return
            with self.store.transaction() as db:
                for r in selected:db.execute('DELETE FROM research_sync_outbox WHERE record_key=?',(r['record_key'],))
            self.uploaded+=len(selected);self.last_ack=self.clock().isoformat();self.failure=None
            self.status='SYNCED' if not self.pending() else 'CATCHING_UP'

    def pending(self):return self.store.read('SELECT count(*) AS n FROM research_sync_outbox')[0]['n']

    def public(self):
        tables={r['name'] for r in self.store.read("SELECT name FROM sqlite_master WHERE type='table'")}
        cursor=self.store.meta('research-sync-tick-cursor',0)
        raw_pending=self.store.read('SELECT count(*) AS n FROM premium_impulse_ticks WHERE id>?',(cursor,))[0]['n'] if 'premium_impulse_ticks' in tables else 0
        return dict(status=self.status,pending_records=self.pending(),uploaded_this_process=self.uploaded,
                    uncaptured_ticks=raw_pending,last_provider_ack=self.last_ack,failure=self.failure,broker_writes=False)

    def run(self):
        while not self.stop.is_set():
            try:self.step()
            except Exception:self.status='ARCHIVE_LOCAL_ERROR';self.failure='ARCHIVE_CAPTURE_UNAVAILABLE'
            self.stop.wait(5)

    def start(self):threading.Thread(target=self.run,daemon=True).start()
