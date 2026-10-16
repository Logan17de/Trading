"""Read-only UI must separate an ordinary wait from missing data/live faults."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_checklist_and_graph_preserve_faults_and_use_only_observed_evidence():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for dashboard presentation checks')
    source = Path(__file__).parents[1] / 'src/nifty_engine/agent_engine/dashboard_assets/dashboard.js'
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const context=vm.createContext({inr:n=>'INR '+n,dateTime:s=>s,known:n=>typeof n==='number'&&Number.isFinite(n)});
vm.runInContext(source.slice(source.indexOf('function algoReason('),source.indexOf('function renderStrategyGraph(')),context);
const now=new Date('2026-10-16T06:30:00Z'),at=now.toISOString();
function base(){return {
 vm:{status:'HEALTHY'},freshness:'RECENT',runtime:{error:null},positions_status:'AVAILABLE',orders_status:'AVAILABLE',
 live_setup:{mode:'live',paused:false,owner_risk_authorized:true},
 normal_data_worker:{status:'CURRENT',checked_at:at},
 execution_controller:{phase:'IDLE',blockers:['REPORT_INPUTS_OR_STRATEGY_SELECTION_REQUIRED'],execution_enabled:false},
 control:{algo:{desired_enabled:true,index:'NIFTY',blockers:['REPORT_INPUTS_OR_STRATEGY_SELECTION_REQUIRED']},
  slot:{new_entry_blocked:false},strategy_controls:{enabled:{normal_theta:true}},
  strategy_rules:{weekday_index_jst:['NIFTY','NIFTY','SENSEX','SENSEX','NIFTY']}},
 normal_theta:{indices:{NIFTY:{at,status:'WAIT',regime:'UNKNOWN',reasons:['NO_CONFIRMED_DIRECTIONAL_TREND'],basket_scan:{status:'NO_CONFIRMED_TREND'}}}},
 markets:[{index:'NIFTY',price:23100,series:[{at:'2026-10-16T06:29:55Z',value:23099},{at,value:23100}]}]
};}
function model(view){context.view=view;context.now=now;return vm.runInContext('algoChecklistModel(view,now)',context);}
function graph(view){context.view=view;context.now=now;return vm.runInContext('strategyGraphModel(view,now)',context);}
let view=base(),before=JSON.stringify(view),m=model(view);
assert.equal(m.heading,'Waiting for NIFTY trend');assert.equal(m.badge,'On · waiting for trend');
assert.equal(m.rows.find(r=>r.title==='Groww basket margin').state,'not_checked');
assert.equal(m.rows.find(r=>r.title==='Confirmed trend').state,'waiting');
assert.equal(JSON.stringify(view),before,'presentation must not change state');
for(const blocker of ['REPOSITORY_PAUSED','BROKER_PROTECTION_RECONCILIATION_REQUIRED','REPORT_EXECUTION_INPUT_UNAVAILABLE','FRESH_COMPLETE_BROKER_STATE_REQUIRED']){
 view=base();view.execution_controller.blockers.push(blocker);m=model(view);
 assert.equal(m.heading,'Trading needs attention');assert.equal(m.tone,'blocked');
}
view=base();view.live_setup.paused=true;assert.equal(model(view).heading,'Trading needs attention');
view=base();view.control.strategy_controls.enabled.normal_theta=false;assert.equal(model(view).heading,'Trading needs attention');
view=base();view.control.algo.desired_enabled=false;assert.equal(model(view).heading,'Algo Off');
view=base();view.freshness='STALE';m=model(view);assert.equal(m.heading,'Waiting for current Oracle data');
assert.equal(m.rows.filter(r=>r.state==='passed').length,0);
view=base();view.normal_data_worker.checked_at='2026-10-16T06:26:00Z';m=model(view);
assert.equal(m.heading,'Trading needs attention');assert.equal(m.rows.find(r=>r.title==='Confirmed trend').state,'unknown');
view=base();view.normal_theta.indices.NIFTY.at='2026-10-16T06:29:00Z';
assert.equal(model(view).heading,'Waiting for current strategy evidence');
view=base();view.normal_theta.indices.NIFTY.reasons.push('CURRENT_TREND_INPUT_REQUIRED:ma20');
assert.equal(model(view).rows.find(r=>r.title==='Confirmed trend').state,'unknown');
view=base();view.execution_controller.phase='REPORT_MONITORING';assert.equal(model(view).heading,'Managing the owned basket');
view.execution_controller.blockers.push('BROKER_PROTECTION_RECONCILIATION_REQUIRED');assert.equal(model(view).tone,'blocked');
view=base();let g=graph(view);assert.equal(g.points.length,2);assert.equal(g.price,23100);assert.deepEqual(Object.keys(g.indicators),[]);
assert.equal(g.points[0].value,23099,'no smoothing/fabrication');
view.normal_theta.indices.NIFTY.trend_inputs={ma20:{status:'AVAILABLE',value:23200},ma50:{status:'UNKNOWN',value:24000},adx14:{status:'AVAILABLE',value:19}};
g=graph(view);assert.equal(g.indicators.ma20,23200);assert.equal(g.indicators.ma50,undefined);assert.equal(g.indicators.adx14,19);
view.freshness='STALE';g=graph(view);assert.equal(g.current,false);assert.equal(g.price,null);assert.deepEqual(Object.keys(g.indicators),[]);
view=base();const entry=view.normal_theta.indices.NIFTY;
entry.status='CANDIDATE';entry.regime='BULLISH';entry.reasons=[];
entry.candidate={expiry:'2026-11-03',spread_type:'bull_put',lots:1,basket_requirement_inr:20000,hedge_requirement_inr:500,
 net_max_expiry_profit_inr:2000,worst_case_loss_inr:500,legs:[{side:'SELL',strike:22500,symbol:'NIFTY_TEST_PE'},{side:'BUY',strike:22450,symbol:'NIFTY_TEST_HEDGE_PE'}]};
view.control.algo.execution_enabled=true;view.execution_controller.execution_enabled=true;
view.control.algo.blockers=[];view.execution_controller.blockers=[];
m=model(view);assert.equal(m.heading,'NIFTY basket qualifies');assert.match(m.basket,/SELL 22500 PUT \+ BUY 22450 hedge/);
assert.match(m.basket,/margin INR 20000/);assert.match(m.basket,/profit INR 2000/);
assert.equal(m.rows.find(r=>r.title==='Groww basket margin').state,'passed');
view.trade_audit={events:[{kind:'ORDER',at,side:'BUY',symbol:'EXACT_OPTION',status:'FULLY_FILLED',filled_quantity:65}]};
assert.match(model(view).activity,/EXACT_OPTION.*filled 65/);
"""
    result = subprocess.run([node, '-e', script, str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
