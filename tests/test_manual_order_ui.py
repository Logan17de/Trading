"""Manual order page is owner-click-only and checks every staged broker action."""
import shutil
import subprocess
from pathlib import Path

import pytest

from nifty_engine.agent_engine.dashboard import handler


def test_manual_page_assets_are_bounded_and_local_token_served():
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from types import SimpleNamespace
    from urllib.request import urlopen
    state=SimpleNamespace(token='TEST_SECRET_TOKEN')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(state))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        uri='http://127.0.0.1:'+str(server.server_port)
        with urlopen(uri+'/live-setup') as r:
            body=r.read().decode()
            assert r.status==200 and 'TEST_SECRET_TOKEN' in body
            assert 'BUY' in body and 'SET SL' in body and 'SELL' in body
            assert r.headers['Cache-Control']=='no-store'
        for path in ('manual-order.css','manual-order.js'):
            with urlopen(uri+'/'+path) as r:
                assert r.status==200 and r.read()
    finally:
        server.shutdown();server.server_close();thread.join(3)


def test_manual_buy_sl_sell_buttons_require_explicit_sequence():
    node=shutil.which('node')
    if not node:pytest.skip('Node required for manual test interaction')
    source=Path(__file__).parents[1]/'src/nifty_engine/agent_engine/dashboard_assets/manual-order.js'
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements=new Map(),calls=[],prompts=[];
function element(){return {events:{},value:'',textContent:'',content:'LOCALTOKEN',
 dataset:{},disabled:false,addEventListener(name,fn){this.events[name]=fn;},
 replaceChildren(){},append(){}}}
function get(key){if(!elements.has(key))elements.set(key,element());return elements.get(key);}
get('meta[name="local-token"]').content='TEST';
const runtime={live_setup:{mode:'paper'},control:{algo:{desired_enabled:false}}};
let saved={status:'NOT_STARTED',allowed_actions:['status','preview']};
const contract={index:'SENSEX',symbol:'SENSEX26O1576000CE',expiry:'2026-10-15',lot_size:20,tick_size:.05};
const ctx=vm.createContext({Intl,Date,Number,Object,Set,Math,window:{
 confirm:prompt=>{prompts.push(prompt);return true;}},document:{
 getElementById:get,querySelector:get,createElement:element},
 fetch:async(url,opt={})=>{
 if(url==='/api/dashboard')return {ok:true,json:async()=>runtime};
 if(!opt.method)return {ok:true,json:async()=>saved};
 const x=JSON.parse(opt.body);calls.push(x);
 if(x.action==='lookup')return {ok:true,json:async()=>({
    status:'OWNER_CONTRACT_FOUND',contract,source:'GROWW_PUBLIC_INSTRUMENT_MASTER',
    quote:{},egress_ip:null})};
 if(x.action==='preview'){
    saved={status:'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT',plan_hash:'a'.repeat(64),
      allowed_actions:['manual-buy','preview'],plan_spec:x.spec};
    return {ok:true,json:async()=>saved};
 }
 if(x.action==='manual-buy'){
    saved={...saved,status:'CHILD_FILLED',allowed_actions:['protect','close','capture']};
    return {ok:true,json:async()=>saved};
 }
 if(x.action==='protect'){
    saved={...saved,status:'STOP_ACTIVE_OBSERVED',allowed_actions:['cancel-stop','capture']};
    return {ok:true,json:async()=>saved};
 }
 if(x.action==='cancel-stop'){
    saved={...saved,status:'STOP_ACTIVE_OBSERVED',allowed_actions:['close','capture']};
    return {ok:true,json:async()=>saved};
 }
 if(x.action==='close'){
    saved={...saved,status:'FLAT_AWAITING_SECOND_READ',allowed_actions:['capture']};
    return {ok:true,json:async()=>saved};
 }
 throw Error('Unexpected '+x.action);
 }});
get('index').value='SENSEX';get('symbol').value=contract.symbol;
get('egress-ip').value='193.123.166.128';
get('buy-price').value='4.00';get('sell-price').value='4.10';
get('sl-price').value='3.90';get('sl-limit').value='3.85';
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
(async()=>{
 await new Promise(setImmediate);
 assert.deepEqual(calls,[]); // opening page cannot buy
 await vm.runInContext('loadContract()',ctx);
 assert.deepEqual(calls.map(x=>x.action),['lookup']);
 assert.equal(get('expiry').value,'2026-10-15');
 assert.equal(get('qty').value,20);
 await vm.runInContext('clickBuy()',ctx);
 assert.deepEqual(calls.map(x=>x.action),['lookup','preview']);
 assert.equal(get('buy').textContent,'CONFIRM BUY');
 await vm.runInContext('clickBuy()',ctx);
 assert.deepEqual(calls.map(x=>x.action),['lookup','preview','manual-buy']);
 assert.equal(get('set-sl').disabled,false);
 await vm.runInContext('clickSL()',ctx);
 assert.equal(calls[3].action,'protect');
 assert.equal(get('sell').textContent,'SELL · remove SL');
 await vm.runInContext('clickSell()',ctx);
 assert.equal(calls[4].action,'cancel-stop');
 assert.equal(get('sell').textContent,'SELL');
 await vm.runInContext('clickSell()',ctx);
 assert.equal(calls[5].action,'close');
 assert.equal(calls.filter(x=>x.action==='manual-buy').length,1);
 assert.equal(calls.filter(x=>x.action==='close').length,1);
 assert(prompts.length>=4);
 // Expired legacy GTT must not silently populate a new direct BUY form.
 get('symbol').value='';get('buy-price').value='';get('sell-price').value='';
 vm.runInContext('populate({contract:{symbol:"NIFTY26O1321850PE",index:"NIFTY",expiry:"2026-10-13",lot_size:65,tick_size:.05},quantity:65,buy_limit_price:30.5,sell_limit_price:33,stop_trigger_price:27,stop_limit_price:27,valid_until:"2026-10-08T08:32:19.294Z"})',ctx);
 assert.equal(get('symbol').value,'');
 assert.equal(get('buy-price').value,'');
 assert.match(get('contract').textContent,/Previous test is expired or a different entry type/);
 assert.equal(calls.filter(x=>x.action==='manual-buy').length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result=subprocess.run([node,'-e',script,str(source)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
