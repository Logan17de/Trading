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
    for asset in ('live-setup.js','live-setup.css'):
        with urlopen(url+'/'+asset) as response:assert response.status==200
    assert calls==[]


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
function element(){return {events:{},dataset:{},content:'TOKEN',checked:false,addEventListener(k,fn){this.events[k]=fn;},replaceChildren(){},append(){}};}
function get(key){if(!elements.has(key))elements.set(key,element());return elements.get(key);}
const writes=['submit','close','cancel','arm'].map(action=>Object.assign(element(),{dataset:{write:action}}));
const reads=['capture','arm-preview'].map(action=>Object.assign(element(),{dataset:{read:action}}));
let response={status:'NOT_STARTED',allowed_actions:['status','preview']};
const c=vm.createContext({Intl,Date,Number,Set,FormData:class{},document:{querySelector:get,
 querySelectorAll:s=>s==='[data-write]'?writes:reads,createElement:element},fetch:async(url,args)=>{
 if(url==='/api/dashboard')return {json:async()=>({live_setup:{mode:'paper'},control:{algo:{desired_enabled:false}}})};
 assert.equal(args.headers['X-Local-Token'],'TOKEN');calls.push(JSON.parse(args.body));return {json:async()=>response};}});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
(async()=>{
 await new Promise(setImmediate);assert.deepEqual(calls,[{action:'status'}]);
 response={status:'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT',plan_hash:'a'.repeat(64),allowed_actions:['submit','status']};
 await vm.runInContext('command({action:"status"})',c);
 assert.equal(writes[0].disabled,true);writes[0].events.click();assert.equal(calls.length,2);
 get('#confirm-action').checked=true;get('#confirm-action').events.change();assert.equal(writes[0].disabled,false);
 writes[0].events.click();await new Promise(setImmediate);
 assert.deepEqual(calls[2],{action:'submit',confirm:'a'.repeat(64)});assert.equal(get('#confirm-action').checked,false);
 c.fetch=async()=>{throw Error('lost response');};
 await vm.runInContext('command({action:"capture"})',c);
 assert.equal(writes.every(b=>b.hidden),true);assert.equal(calls.length,3);
 assert.match(get('#command-note').textContent,/No automatic retry/);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result=subprocess.run([node,'-e',script,str(source)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
