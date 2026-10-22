"""Fixed index scales must not drift, distort indicators or alter other charts."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_fixed_session_ranges_labels_gaps_and_reload_persistence():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for chart presentation checks')
    source = Path(__file__).parents[1] / 'src/nifty_engine/agent_engine/dashboard_assets/dashboard.js'
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),storage=new Map();
const sessionStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};
function setup(){
 const context=vm.createContext({sessionStorage,document:{createElement:()=>({})}});
 vm.runInContext(source.slice(0,source.indexOf('function renderIndices(')),context);
 vm.runInContext(source.slice(source.indexOf('function strategyGraphRange('),source.indexOf('function strategyGraphModel(')),context);
 return context;
}
let context=setup();
function range(index,rows){context.index=index;context.rows=rows;return vm.runInContext('strategyGraphRange(index,rows)',context);}
const rows=[{at:'2026-10-19T03:45:00Z',value:23000.25},{at:'2026-10-19T03:45:05Z',value:23004},{at:'2026-10-19T03:47:00Z',value:23009}];
let fixed=range('NIFTY',rows);
assert.equal(fixed.low,22500.25);assert.equal(fixed.high,23500.25);
assert.equal(range('NIFTY',rows.slice(1)).start,23000.25,'rolling cache must not move the scale');
context=setup();
assert.equal(range('NIFTY',rows.slice(1)).start,23000.25,'refresh must retain the session anchor');
let sensex=range('SENSEX',[{at:rows[0].at,value:74000}]);
assert.equal(sensex.low,72500);assert.equal(sensex.high,75500);
assert.equal(range('NIFTY',[]),null);assert.equal(range(null,rows),null);
function draw(width=900,options={}){
 const container={id:'strategy-graph',clientWidth:width,clientHeight:285,dataset:{},classList:{contains:()=>false},append:()=>{}};
 const lines=[{key:'spot',label:'NIFTY',name:'Observed index price',color:'#2473b5',points:rows},
  {key:'ma20',name:'Daily MA20',color:'#9b62b6',points:rows.map(r=>({...r,value:22900}))},
  {key:'ma50',name:'Daily MA50',color:'#d18221',points:rows.map(r=>({...r,value:25000}))}];
 context.container=container;context.points=rows;
 context.options={lines,indexPoints:true,fiveMinuteGrid:true,gapSeconds:20,fixedRange:fixed,...options};
 vm.runInContext('chart(container,points,options)',context);return container.innerHTML;
}
for(const width of [900,360]){
 const svg=draw(width);
 assert.match(svg,/data-y-min="22500.25" data-y-max="23500.25"/);
 assert.match(svg,/clip-path="url\(#plot-strategy-graph\)"/);
 assert.equal((svg.match(/data-axis-value=/g)||[]).length,5);
 assert.match(svg,/data-series-label="spot" data-outside=""/);
 assert.match(svg,/data-series-label="ma20" data-outside=""/);
 assert.match(svg,/data-series-label="ma50" data-outside="above"/);
 assert.match(svg,/MA50 25,000.00 ↑/,'off-scale references keep their true value');
 const spotPath=svg.match(/data-series="spot" d="([^"]+)"/)[1];
 assert.equal((spotPath.match(/M/g)||[]).length,2,'outage must not be bridged');
 assert.equal((spotPath.match(/L/g)||[]).length,1);
 assert.ok(!svg.includes('NaN'));
}
assert.ok(!draw(900,{fixedRange:null}).includes('data-y-min='),'ordinary charts retain their automatic scale');
const missing=draw(900,{lines:[{key:'spot',name:'Index',color:'#2473b5',points:rows}]});
assert.ok(!missing.includes('data-series-label="ma20"'),'missing indicators must not be invented');
let next=range('NIFTY',[{at:'2026-10-19T15:01:00Z',value:23200}]);
assert.equal(next.start,23200,'JST date rollover resets the anchor');
context=setup();
assert.equal(range('NIFTY',[{at:'2026-10-20T03:45:00Z',value:23210}]).start,23200);
storage.set('trading-strategy-anchors-v1',JSON.stringify({session:'2026-10-20',anchors:{NIFTY:{value:-1,at:'invalid'}}}));
context=setup();
assert.equal(range('NIFTY',[{at:'2026-10-20T03:45:00Z',value:23210}]).start,23210,'invalid saved anchors must not affect the range');
"""
    result = subprocess.run([node, '-e', script, str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
