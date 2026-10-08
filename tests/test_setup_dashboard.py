"""Loopback setup controls: explicit authenticated actions, no broker calls."""
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from nifty_engine.agent_engine.dashboard import handler


@pytest.fixture
def app():
    calls=[]
    state=SimpleNamespace(token='private-local-test-token',setup_command=lambda body:
                          calls.append(body) or {'status':'TEST_ONLY','allowed_actions':[]})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(state))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',state,calls
    server.shutdown();server.server_close();thread.join(timeout=2)


def test_setup_page_only_reads_and_injects_protected_local_token(app):
    url,state,calls=app
    with urlopen(url+'/live-setup') as response:
        page=response.read().decode('utf-8-sig')
        assert state.token in page and '__LOCAL_TOKEN__' not in page
        assert 'frame-ancestors' in response.headers['Content-Security-Policy']
        assert response.headers['Cache-Control']=='no-store'
        assert 'type="datetime-local"' not in page
        assert '30 minutes to submit the BUY' in page
        assert 'aria-describedby="preview-feedback"' in page
    for asset in ('live-setup.js','live-setup.css'):
        with urlopen(url+'/'+asset) as response:assert response.status==200
    assert calls==[]


def test_setup_saved_get_is_local_only_and_never_dispatches_command(app):
    url,state,calls=app
    state.setup_status=lambda:dict(status='ACTIVE_PARENT_OBSERVED',status_source='SAVED_LOCAL_STATE',operation=None)
    with urlopen(url+'/api/live-setup') as response:
        assert json.load(response)['status_source']=='SAVED_LOCAL_STATE'
        assert response.headers['Cache-Control']=='no-store'
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url+'/api/live-setup',headers={'Origin':'https://foreign.invalid'}))
    assert error.value.code==403 and calls==[]


def test_setup_post_refuses_foreign_sites_and_missing_token_before_dispatch(app):
    url,state,calls=app
    for extra in ({},{'X-Local-Token':state.token,'Origin':'https://foreign.invalid'},
                  {'X-Local-Token':state.token,'Sec-Fetch-Site':'cross-site'}):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url+'/api/live-setup',data=b'{"action":"arm"}',
                headers={'Content-Type':'application/json',**extra},method='POST'))
        assert error.value.code==403
    assert calls==[]


def test_setup_exact_authenticated_post_and_invalid_content(app):
    url,state,calls=app
    headers={'Content-Type':'application/json','X-Local-Token':state.token}
    body={'action':'status'}
    with urlopen(Request(url+'/api/live-setup',data=json.dumps(body).encode(),headers=headers)) as response:
        assert json.load(response)['status']=='TEST_ONLY'
    assert calls==[body]
    for data,kind in ((b'{}','text/plain'),(b'x','application/json'),(b' '*8193,'application/json')):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url+'/api/live-setup',data=data,headers={**headers,'Content-Type':kind}))
        assert error.value.code==400
    assert calls==[body]


def test_setup_unknown_result_never_claims_no_broker_write(app):
    url,state,_=app
    def failed(_):raise RuntimeError('PRIVATE_PROVIDER_EXCEPTION')
    state.setup_command=failed
    with pytest.raises(HTTPError) as error:
        urlopen(Request(url+'/api/live-setup',data=b'{"action":"status"}',
            headers={'Content-Type':'application/json','X-Local-Token':state.token}))
    result=json.loads(error.value.read())
    assert result['status']=='SETUP_RESULT_UNKNOWN' and 'broker_writes' not in result
    assert 'PRIVATE' not in str(result)


def test_setup_ui_never_autosubmits_and_binds_explicit_click_to_exact_hash():
    node=shutil.which('node')
    if not node:pytest.skip('Node required for setup interaction replay')
    source=Path(__file__).parents[1]/'src/nifty_engine/agent_engine/dashboard_assets/live-setup.js'
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements=new Map(),calls=[];
function element(){return {events:{},dataset:{},content:'TOKEN',checked:false,addEventListener(k,fn){this.events[k]=fn;},replaceChildren(){},append(){},focus(){this.focused=true;},scrollIntoView(){this.scrolled=true;}};}
function get(key){if(!elements.has(key))elements.set(key,element());return elements.get(key);}
const writes=['submit','close','cancel','arm','buy-protect'].map(action=>Object.assign(element(),{dataset:{write:action}}));
const reads=['capture','arm-preview'].map(action=>Object.assign(element(),{dataset:{read:action}}));
let response={status:'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT',plan_hash:'a'.repeat(64),allowed_actions:['submit','status','preview']};
let now=Date.parse('2026-10-08T05:00:00Z');
class Clock extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
const c=vm.createContext({Intl,Date:Clock,Number,Set,setInterval,clearInterval,setTimeout:()=>0,clearTimeout:()=>{},FormData:class{constructor(fields){this.fields=fields;}get(key){return this.fields[key];}},document:{querySelector:get,
 querySelectorAll:s=>s==='[data-write]'?writes:reads,createElement:element},fetch:async(url,args)=>{
 if(url==='/api/dashboard')return {json:async()=>({live_setup:{mode:'paper'},control:{algo:{desired_enabled:false}}})};
 if(!args.method)return {json:async()=>response};
 assert.equal(args.headers['X-Local-Token'],'TOKEN');calls.push(JSON.parse(args.body));return {json:async()=>response};}});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
(async()=>{
 await new Promise(setImmediate);assert.deepEqual(calls,[]);
 assert.match(get('#command-note').textContent,/No broker request/);
 get('#confirm-action').checked=true;get('#confirm-action').events.change();
 assert.equal(writes[0].disabled,true);writes[0].events.click();assert.equal(calls.length,0);
 response={status:'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT',plan_hash:'a'.repeat(64),allowed_actions:['submit','status']};
 await vm.runInContext('command({action:"status"})',c);
 assert.equal(writes[0].disabled,true);writes[0].events.click();assert.equal(calls.length,1);
 get('#confirm-action').checked=true;get('#confirm-action').events.change();assert.equal(writes[0].disabled,false);
 writes[0].events.click();await new Promise(setImmediate);
 assert.deepEqual(calls[1],{action:'submit',confirm:'a'.repeat(64)});assert.equal(get('#confirm-action').checked,false);
 c.fetch=async()=>{throw Error('lost response');};
 await vm.runInContext('command({action:"capture"})',c);
 assert.equal(writes.every(b=>b.hidden),true);assert.equal(calls.length,2);
 assert.match(get('#command-note').textContent,/No automatic retry/);
 // User-supplied prices stay exact. Only an explicit new preview creates a
 // deadline; loading the page/restoring a saved plan cannot renew it or trade.
 const previews=[];
 c.fetch=async(url,args)=>{
   if(url==='/api/dashboard')return {json:async()=>({})};
   const body=JSON.parse(args.body);assert.equal(body.action,'preview');previews.push(body);
   return {json:async()=>({status:'TEST_PREVIEW',allowed_actions:['preview','status'],plan_spec:body.spec})};
 };
 vm.runInContext('current={allowed_actions:["preview","status"]};savedOnly=true;render()',c);
 const form={symbol:'SENSEX26O0871700PE',index:'SENSEX',expiry:'2026-10-08',lot_size:'20',tick_size:'0.05',
   trigger_price:'28',buy_limit_price:'28.5',sell_limit_price:'30',stop_trigger_price:'24',stop_limit_price:'23.8',egress_ip:'8.8.8.8'};
 const preview=()=>get('#plan-form').events.submit({preventDefault(){},currentTarget:form});
 preview();await new Promise(setImmediate);assert.equal(previews.length,0);
 assert.match(get('#setup-reason').textContent,/expiring after today/);
 assert.match(get('#preview-feedback').textContent,/Preview blocked:.*2026-10-08/);
 assert.equal(get('#preview-feedback').hidden,false);assert.equal(get('#preview-feedback').dataset.kind,'error');
 assert.equal(get('#preview-feedback').focused,true);assert.equal(get('#preview-feedback').scrolled,true);
 get('#plan-form').events.input();assert.equal(get('#preview-feedback').hidden,true);
 form.expiry='2026-10-15';form.symbol='SENSEX26O1571700PE';
 preview();await new Promise(setImmediate);assert.equal(previews.length,1);
 assert.equal(previews[0].spec.valid_until,'2026-10-08T05:30:00.000Z');
 assert.equal(previews[0].spec.buy_limit_price,28.5);assert.equal(previews[0].spec.stop_limit_price,23.8);
 now+=120000;
 vm.runInContext('restoreForm(current.plan_spec);render()',c);
 assert.equal(previews.length,1);assert.equal(previews[0].spec.valid_until,'2026-10-08T05:30:00.000Z');
 preview();await new Promise(setImmediate);assert.equal(previews.length,2);
 assert.equal(previews[1].spec.valid_until,'2026-10-08T05:32:00.000Z');
 assert.equal(calls.length,2);
 let finish;
 c.fetch=async(url,args)=>url==='/api/dashboard'?{json:async()=>({})}:new Promise(resolve=>{finish=resolve;});
 preview();assert.equal(get('#preview-button').disabled,true);assert.equal(get('#preview-button').textContent,'Checking preview…');
 assert.match(get('#preview-feedback').textContent,/Checking preview/);
 finish({json:async()=>({status:'OWNER_SETUP_BLOCKED',reason:'COMMISSIONING_TRIGGER_MUST_START_ABOVE_MARKET',allowed_actions:['preview','status']})});
 await new Promise(setImmediate);assert.match(get('#preview-feedback').textContent,/current ask/);
 assert.equal(get('#preview-button').disabled,false);
 c.fetch=async()=>({json:async()=>({status:'NOT_STARTED',allowed_actions:['preview','status']})});
 await vm.runInContext('command({action:"status"})',c);
 assert.match(get('#command-note').textContent,/Load the contract/);
 get('#plan-form').events.invalid({target:{labels:[{textContent:'BUY limit ₹'}],validationMessage:'Please fill out this field.'}});
 assert.match(get('#preview-feedback').textContent,/Check BUY limit/);
 // The direct mode requires a reviewed plan but no redundant checkbox. Page
 // reads never submit; only the Buy + protect click carries its exact hash.
 c.fetch=async(url,args)=>{
   if(url==='/api/dashboard')return {json:async()=>({live_setup:{supports_direct_test:true,mode:'paper'}})};
   calls.push(JSON.parse(args.body));
   return {json:async()=>({status:'OWNER_TEST_RUNNING',allowed_actions:['status','capture'],plan_hash:'b'.repeat(64)})};
 };
 vm.runInContext('current={status:"OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT",plan_hash:"b".repeat(64),allowed_actions:["buy-protect","status"]};savedOnly=true;render()',c);
 const buy=writes[4];assert.equal(buy.disabled,true);buy.events.click();assert.equal(calls.length,2);
 vm.runInContext('savedOnly=false;render()',c);
 assert.equal(buy.disabled,false);assert.equal(get('#confirm-row').hidden,true);
 buy.events.click();buy.events.click();await new Promise(setImmediate);
 assert.equal(calls.length,3);assert.deepEqual(calls[2],{action:'buy-protect',confirm:'b'.repeat(64)});
 assert.equal(buy.hidden,true);assert.match(get('#command-note').textContent,/job started on Oracle/);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result=subprocess.run([node,'-e',script,str(source)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
