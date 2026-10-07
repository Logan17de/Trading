"""Bounded official scheduled-event evidence. Never infer clear from a failed fetch."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .contracts import IST, stamp
from .execution import fresh

FED='https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
RBI='https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=62422'
MOSPI='https://www.mospi.gov.in/uploads/documents/releaseCalender/1788266096813-ARC%202026-27%20updated%20till%20August%202026.pdf'
MOSPI_INDEX='https://www.mospi.gov.in/api/documents/get-latest-release-calender'
MOSPI_HASH='33156099f6e9c4767d71f948112a23c5ed56b59af8a24c49e8c67ab419ed8172'
MONTHS={m:i+1 for i,m in enumerate(('January','February','March','April','May','June','July','August','September','October','November','December'))}
KEY='report-official-event-calendar'


class Text(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,text):self.parts.append(text)
    def value(self):return ' '.join(' '.join(self.parts).split())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def fetch(url):
    if url not in (FED,RBI,MOSPI,MOSPI_INDEX):raise ValueError('FIXED_OFFICIAL_SOURCE_REQUIRED')
    request=urllib.request.Request(url,headers={'User-Agent':'Trading-Research/1.0'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=8) as response:
        raw=response.read(2_000_001)
        if response.status!=200 or len(raw)>2_000_000:raise ValueError('BOUNDED_OFFICIAL_RESPONSE_REQUIRED')
    return raw


def event(kind,day,tz):
    # Source supplies dates, not an exact announcement time. Block the whole
    # source-local day; timezone conversion can span two JST calendar dates.
    at=datetime.combine(day,time.min,tz)
    return dict(kind=kind,start_at=at.isoformat(),end_at=(at+timedelta(days=1)).isoformat(),time_known=False)


def fed_events(raw,year):
    text=raw.decode('utf-8')
    marker=f'{year} FOMC Meetings'
    if text.count(marker)!=1:raise ValueError('FOMC_YEAR_SECTION_REQUIRED')
    section=text.split(marker,1)[1].split(f'{year-1} FOMC Meetings',1)[0]
    months=re.findall(r'class="[^"]*fomc-meeting__month[^\"]*"[^>]*>\s*<strong>([A-Za-z]+)</strong>',section)
    dates=re.findall(r'class="[^"]*fomc-meeting__date[^\"]*"[^>]*>\s*(\d{1,2})-(\d{1,2})\*?\s*</div>',section)
    if len(months)!=8 or len(dates)!=8:raise ValueError('EIGHT_FOMC_MEETINGS_REQUIRED')
    rows=[]
    for month,(first,last) in zip(months,dates):
        for day in range(int(first),int(last)+1):rows.append(event('FOMC',date(year,MONTHS[month],day),ZoneInfo('America/New_York')))
    if len(rows)!=16:raise ValueError('COMPLETE_FOMC_DATES_REQUIRED')
    return rows,date(year,12,31)


def rbi_events(raw):
    parser=Text();parser.feed(raw.decode('utf-8'));text=parser.value()
    marker='Dates of meetings of Monetary Policy Committee for 2026-27'
    if marker not in text:raise ValueError('RBI_SCHEDULE_TITLE_REQUIRED')
    section=text.split(marker,1)[1].split('Chief General Manager',1)[0]
    matches=re.findall(r'([A-Za-z]+) (\d{1,2}), (\d{1,2}) and (\d{1,2}), (202[67])',section)
    if len(matches)!=6:raise ValueError('SIX_RBI_MEETINGS_REQUIRED')
    rows=[]
    for month,a,b,c,year in matches:
        for day in (a,b,c):rows.append(event('RBI_MPC',date(int(year),MONTHS[month],int(day)),IST))
    if len({r['start_at'] for r in rows})!=18:raise ValueError('UNIQUE_RBI_DATES_REQUIRED')
    return rows,date(2027,3,31)


def mospi_events(raw,index_page):
    # Reviewed dates belong to this exact official PDF, not recurring guesses.
    # A revised document/link requires review before it can certify coverage.
    latest=json.loads(index_page)
    if (hashlib.sha256(raw).hexdigest()!=MOSPI_HASH or not raw.startswith(b'%PDF-')
            or latest.get('status')!='success'
            or latest.get('data',{}).get('url')!=MOSPI.split('.gov.in/',1)[1].replace('%20',' ')):
        raise ValueError('REVIEWED_MOSPI_DOCUMENT_REQUIRED')
    rows=[]
    for year,month in [(2026,m) for m in (10,11,12)]+[(2027,m) for m in (1,2,3)]:
        rows.append(event('INDIA_CPI',date(year,month,12),IST))
        rows.append(event('INDIA_IIP',date(year,month,28),IST))
    for day in ('2026-11-30','2027-01-07','2027-02-26'):
        rows.append(event('INDIA_GDP',date.fromisoformat(day),IST))
    return rows,date(2027,3,31)


def assessment(sources,now,holding_days=38):
    through=now.astimezone(IST).date()+timedelta(days=holding_days)
    horizon=datetime.combine(through+timedelta(days=1),time.min,IST)
    unknown=[];risks=[]
    for name in ('RBI','FED','MOSPI'):
        source=sources.get(name,{})
        try:
            if (source['status']!='AVAILABLE' or not fresh(source['checked_at'],now,3600)
                    or date.fromisoformat(source['coverage_through'])<through
                    or date.fromisoformat(source.get('coverage_from','2026-10-01'))>now.astimezone(IST).date()):raise ValueError()
            risks.extend(dict(r,source=name) for r in source['events'] if stamp(r['end_at'])>now and stamp(r['start_at'])<horizon)
        except (KeyError,ValueError,TypeError):unknown.append(name)
    checked=min((s['checked_at'] for s in sources.values() if s.get('checked_at')),default=now.isoformat())
    return dict(status='UNKNOWN' if unknown else 'EVENT_RISK' if risks else 'VERIFIED_CLEAR',
        source='OFFICIAL_RBI_FOMC_MOSPI_SCHEDULES_V1',checked_at=checked,
        coverage_start=now.isoformat(),coverage_through=through.isoformat(),
        unknown_sources=unknown,events=sorted(risks,key=lambda r:r['start_at']),
        scope=['RBI_MPC','FOMC','INDIA_CPI','INDIA_IIP','INDIA_GDP'],broker_writes=False)


class OfficialCalendar:
    def __init__(self,store,*,fetcher=fetch):self.store,self.fetch=store,fetcher
    def check(self,now):
        old=self.store.meta(KEY,{})
        try:
            if fresh(old['checked_at'],now,1800):return old
        except (KeyError,TypeError,ValueError):pass
        sources={}
        for name,url,parse in [('RBI',RBI,rbi_events),('FED',FED,lambda raw:fed_events(raw,now.year)),
                ('MOSPI',MOSPI,lambda raw:mospi_events(raw,self.fetch(MOSPI_INDEX)))]:
            try:
                raw=self.fetch(url);rows,end=parse(raw)
                sources[name]=dict(status='AVAILABLE',checked_at=now.isoformat(),coverage_through=end.isoformat(),
                    coverage_from='2026-10-01',document_sha256=hashlib.sha256(raw).hexdigest(),events=rows)
                if name=='MOSPI':sources[name]['holiday_shifts']='NEXT_WORKING_DAY_NOT_INFERRED'
            except Exception:sources[name]=dict(status='UNKNOWN',checked_at=now.isoformat(),reason='OFFICIAL_SOURCE_FETCH_OR_SCHEMA_UNAVAILABLE')
        value=assessment(sources,now);value['sources']=sources
        self.store.set_meta(KEY,value)
        return value
