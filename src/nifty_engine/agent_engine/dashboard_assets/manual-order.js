/* Local owner-only manual LIMIT order smoke test. No automatic broker write from page reads. */
'use strict';
const el=id=>document.getElementById(id);
const token=document.querySelector('meta[name="local-token"]').content;
let busy=false, saved={allowed_actions:[]}, verified=null, pendingLookup=false;
const rupees=n=>Number.isFinite(Number(n))?'₹'+Number(n).toFixed(2):'—';
const labels={
 COMMISSIONING_REQUIRES_ALGO_OFF:'Turn global Algo Off first.',
 COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG:'The preview window expired or the contract expiry is not eligible for this test.',
 COMMISSIONING_CURRENT_MASTER_MISMATCH:'The symbol does not match the latest Groww instrument master.',
 COMMISSIONING_RECENT_OPTION_QUOTE_REQUIRED:'Oracle could not verify a recent option quote.',
 COMMISSIONING_CANCEL_STOP_BEFORE_CLOSE:'The SL must be cancelled and confirmed inactive before SELL.',
 COMMISSIONING_STOP_MUST_START_BELOW_MARKET:'The SL trigger is already at or above the current bid. Check Groww before submitting.',
 COMMISSIONING_STOP_MUST_BE_BELOW_ENTRY:'SL trigger must be below the actual BUY fill price.',
 COMMISSIONING_FULL_UNCLOSED_LONG_REQUIRED:'Wait for one full BUY lot to fill. Partial fills need manual review.',
 COMMISSIONING_DIRECT_TARGET_MUST_EXCEED_BUY_LIMIT:'For this test, enter a SELL limit above BUY. Prices are not changed automatically.',
 COMMISSIONING_PRICE_NOT_ON_TICK:'Use increments of the exact tick size (typically ₹0.05).',
 EXISTING_PLAN_REQUIRES_REVIEW:'A previous test must be reconciled before starting another. Use Refresh or Advanced setup.',
 READ_CURRENT_PLAN_BEFORE_ANOTHER_WRITE:'Read broker status before sending another action.',
 COMMISSIONING_CREATE_RECONCILIATION_REQUIRED:'Broker result uncertain. Do not repeat BUY. Inspect the original order in Groww.',
 OWNER_SETUP_RESULT_UNKNOWN:'The result may be uncertain. Check the existing order before another action.',
 TURN_ALGO_OFF_BEFORE_SETUP:'Global Algo intent is On. Switch Algo Off before sending a real order preview. The contract lookup itself can still work.'
};
const message=(text,kind='info')=>{el('feedback').dataset.kind=kind;el('feedback').textContent=text;};
const title=x=>String(x||'Unknown').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
function setBusy(value){busy=value;for(const id of ['load','refresh','cancel','retire'])el(id).disabled=value;render();}
function readValues(){
 const ids=['index','symbol','expiry','qty','tick','egress-ip','buy-price','sell-price','sl-price','sl-limit'];
 return Object.fromEntries(ids.map(key=>[key,String(el(key).value).trim()]));
}
function exactContract(){
 const v=readValues();
 return !!verified && verified.index===v.index && verified.symbol===v.symbol &&
   verified.expiry===v.expiry && verified.lot_size===Number(v.qty) &&
   Number(verified.tick_size)===Number(v.tick);
}
function isPositiveTick(value,tick){
 const v=Number(value),t=Number(tick);
 return Number.isFinite(v)&&Number.isFinite(t)&&v>0&&t>0&&
   Math.abs(v/t-Math.round(v/t))<1e-7;
}
function exactSpec(){
 const v=readValues();
 if(!exactContract())throw Error('Load the exact contract first. Prices are never reused with another symbol.');
 if(!v['egress-ip'])throw Error('Enter the Oracle IPv4 approved in Groww (Contract and connection).');
 const tick=Number(v.tick);
 for(const name of ['buy-price','sell-price','sl-price','sl-limit'])
   if(!isPositiveTick(v[name],tick))throw Error('Check '+name+': positive multiple of ₹'+tick+' required.');
 if(Number(v['sell-price'])<=Number(v['buy-price']))
   throw Error('For the protected test, SELL limit must be above BUY limit.');
 if(Number(v['sl-limit'])>Number(v['sl-price']) || Number(v['sl-price'])>=Number(v['buy-price']))
   throw Error('SL SELL limit must be at/below SL trigger, below the BUY limit.');
 const limit=Number(v['buy-price']);
 return {entry_mode:'LIMIT',contract:{
  index:v.index,symbol:v.symbol,expiry:v.expiry,lot_size:Number(v.qty),tick_size:tick
 },quantity:Number(v.qty),trigger_price:limit,buy_limit_price:limit,
 sell_limit_price:Number(v['sell-price']),stop_trigger_price:Number(v['sl-price']),
 stop_limit_price:Number(v['sl-limit']),valid_until:new Date(Date.now()+30*60000).toISOString(),
 egress_ip:v['egress-ip']};
}
function matchesPreview(){
 const spec=saved.plan_spec;
 if(!spec || spec.entry_mode!=='LIMIT'|| !exactContract() || !saved.plan_hash) return false;
 const v=readValues(),c=spec.contract||{};
 const remaining=Date.parse(spec.valid_until)-Date.now();
 return Number.isFinite(remaining) && remaining>0&&remaining<=30*60000
   && c.index===v.index && c.symbol===v.symbol && c.expiry===v.expiry
   && c.lot_size===Number(v.qty) && Number(c.tick_size)===Number(v.tick)
   && spec.quantity===Number(v.qty) && spec.egress_ip===v['egress-ip']
   && Number(spec.buy_limit_price)===Number(v['buy-price'])
   && Number(spec.sell_limit_price)===Number(v['sell-price'])
   && Number(spec.stop_trigger_price)===Number(v['sl-price'])
   && Number(spec.stop_limit_price)===Number(v['sl-limit']);
}
async function api(body){
 const response=await fetch('/api/live-setup',{method:'POST',headers:{
 'Content-Type':'application/json','X-Local-Token':token},
 body:JSON.stringify(body),cache:'no-store'});
 const data=await response.json();
 if(!response.ok)throw Error(title(data.reason||data.status||'Request failed'));
 return data;
}
function render(){
 const allowed=new Set(saved.allowed_actions||[]);
 const canPreview=allowed.has('preview');
 const buyReady=allowed.has('manual-buy')&&saved.status==='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'&&matchesPreview();
 const canStart=canPreview||buyReady||!verified;
 const canSell=allowed.has('cancel-stop')||allowed.has('close');
 // After the first BUY attempt, the test binds every button to the immutable
 // saved contract and exact prices. Editing fields must never silently change
 // the SELL or SL that Oracle will execute.
 const locked=!!saved.plan_spec && !allowed.has('preview') &&
   saved.status!=='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT';
 for(const id of ['index','symbol','buy-price','sell-price','sl-price','sl-limit',
    'egress-ip'])el(id).disabled=busy||locked;
 el('buy').disabled=busy||!canStart;
 el('set-sl').disabled=busy||!allowed.has('protect');
 el('sell').disabled=busy||!canSell;
 el('buy').textContent=buyReady?'CONFIRM BUY':'BUY';
 el('sell').textContent=allowed.has('cancel-stop')?'SELL · remove SL':'SELL';
 el('cancel').disabled=busy||!allowed.has('cancel');
 el('retire').disabled=busy||!allowed.has('retire');
 el('state').textContent=title(saved.status||'Not started');
 el('reason').textContent=labels[saved.reason]||title(saved.reason||'');
 const s=saved.plan_spec;
 el('order-summary').replaceChildren();
 if(s){const rows=[['Option',s.contract.symbol],['Quantity',s.quantity+' (one lot)'],
 ['BUY',rupees(s.buy_limit_price)],['SELL',rupees(s.sell_limit_price)],
 ['SL trigger',rupees(s.stop_trigger_price)],['SL limit',rupees(s.stop_limit_price)]];
 for(const [key,value] of rows){
  const cell=document.createElement('div'),head=document.createElement('b'),val=document.createElement('span');
  head.textContent=key;val.textContent=String(value);cell.append(head,val);el('order-summary').append(cell);
 }}
}
function populate(spec){
 if(!spec)return;
 const x=spec.contract||{},data={
 index:x.index,symbol:x.symbol,expiry:x.expiry,qty:spec.quantity,tick:x.tick_size,
 'egress-ip':spec.egress_ip,'buy-price':spec.buy_limit_price,'sell-price':spec.sell_limit_price,
 'sl-price':spec.stop_trigger_price,'sl-limit':spec.stop_limit_price};
 for(const [k,v] of Object.entries(data))if(v!=null&&el(k))el(k).value=v;
 // A persisted draft is not fresh live instrument master confirmation.
 verified=null;
 el('contract').textContent='Saved plan loaded. Press Load exact contract to verify it before another BUY.';
}
async function initial(){
 try{
  const r=await fetch('/api/live-setup',{cache:'no-store'});
  if(!r.ok)throw Error('Cannot load saved test');
  saved=await r.json();populate(saved.plan_spec||saved.draft_spec);
  message(saved.plan_spec?'Saved broker test loaded. Refresh broker status before further action.':'Ready. Enter the trading symbol and load the exact contract.');
 }catch{message('Local setup status unavailable. No order can be submitted.','error');}
 render();
 refreshRuntime();
}
async function refreshRuntime(){
 try{
  const r=await fetch('/api/dashboard',{cache:'no-store'}),d=await r.json();
  const mode=d.live_setup?.mode||'Unknown';
  const algo=d.control?.algo?.desired_enabled;
  el('runtime-mode').textContent=mode==='paper'?'Paper mode · Algo '+(algo?'On':'Off'):'Mode '+mode;
 }catch{el('runtime-mode').textContent='Oracle status unknown';}
}
async function loadContract(){
 if(busy)return;
 const requested={index:el('index').value,symbol:el('symbol').value.trim().toUpperCase()};
 if(!/^(NIFTY|SENSEX)[A-Z0-9]{3,45}(CE|PE)$/.test(requested.symbol) || !requested.symbol.startsWith(requested.index)){
  message('Enter the exact NIFTY or SENSEX CE/PE trading symbol first.','error');return;
 }
 setBusy(true);message('Checking instrument master. No order will be placed.');
 try{
  const data=await api({action:'lookup',spec:requested});
  if(data.status!=='OWNER_CONTRACT_FOUND')throw Error(labels[data.reason]||title(data.reason||data.status));
  const c=data.contract;
  if(c.index!==requested.index||c.symbol!==requested.symbol)throw Error('Contract mismatch; rejected');
  for(const [key,val] of Object.entries({expiry:c.expiry,qty:c.lot_size,tick:c.tick_size}))el(key).value=val;
  if(data.egress_ip)el('egress-ip').value=data.egress_ip;
  verified={...c};
  const noQuote=data.source==='GROWW_PUBLIC_INSTRUMENT_MASTER';
  el('contract').textContent=c.index+' · '+c.expiry+' · 1 lot = '+c.lot_size+
    ' · Tick '+rupees(c.tick_size)+(noQuote?
    ' · Listed instrument only. Live quote unavailable until Oracle permits read.':
    ' · Bid '+rupees(data.quote?.bid_price)+' · Ask '+rupees(data.quote?.offer_price));
  message(noQuote?'Contract verified in Groww public master. The live quote is not verified. Set your prices, then press BUY to request the read-only broker preview.':
    'Contract and quote loaded. Enter your chosen prices and press BUY to preview.','ok');
 }catch(e){
  verified=null;
  for(const k of ['expiry','qty','tick'])el(k).value='';
  el('contract').textContent='Contract not verified; no order can be sent.';
  message('Lookup failed: '+e.message+'. No order sent.','error');
 }finally{setBusy(false);}
}
async function action(body, verb){
 if(busy)return;
 setBusy(true);message(verb+' in progress. Do not repeat the request.');
 try{
  const result=await api(body);saved=result;
  if(result.status==='OWNER_SETUP_RESULT_UNKNOWN'||result.requires_reconciliation){
    message('Result uncertain. Read broker status and inspect the original Groww order. Do not repeat a write.','error');
  }else if(result.status==='OWNER_SETUP_BLOCKED'||result.reason){
    message(verb+' blocked: '+(labels[result.reason]||title(result.reason||result.status))+'. No retry sent.','error');
  }else{
    message(verb+': '+title(result.status)+'. Acceptance is not proof of fill. Refresh Groww before the next action.','ok');
  }
 }catch(e){message(verb+' response unavailable: '+e.message+'. Check Groww before retrying anything.','error');}
 finally{setBusy(false);refreshRuntime();}
}
async function clickBuy(){
 if(busy)return;
 if(!exactContract()){await loadContract();return;}
 let spec;
 try{spec=exactSpec();}catch(e){message(e.message,'error');return;}
 const allowed=new Set(saved.allowed_actions||[]);
 if(saved.status==='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT' && allowed.has('manual-buy') && matchesPreview()){
  const debit=Number(el('qty').value)*Number(el('buy-price').value);
  if(!window.confirm('REAL LIMIT BUY · '+spec.contract.symbol+' · '+spec.quantity+' units @ '+
     rupees(spec.buy_limit_price)+' (up to '+rupees(debit)+' + charges). No SL is attached until SET SL succeeds. Send exactly one BUY?'))return;
  await action({action:'manual-buy',confirm:saved.plan_hash},'BUY');return;
 }
 if(!allowed.has('preview')){message('An earlier order or test is not settled. Refresh the original broker status; do not start a second BUY.','error');return;}
 if(!window.confirm('READ-ONLY PREVIEW: verify '+spec.contract.symbol+' and your four exact limit/SL prices with Groww. No real order will be sent. Continue?'))return;
 await action({action:'preview',spec},'BUY preview');
 if(saved.status==='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'){
  message('Broker preview passed. Press CONFIRM BUY and approve the real one-lot LIMIT BUY. No order has been sent yet.','ok');
 }
}
async function clickSL(){
 if(!saved.plan_hash||!(saved.allowed_actions||[]).includes('protect'))return;
 if(!window.confirm('REAL STOP-LOSS: submit the exact one-lot protective SELL DOWN GTT for the verified filled long? Stop-limit may not fill.'))return;
 await action({action:'protect',confirm:saved.plan_hash},'SET SL');
}
async function clickSell(){
 if(!saved.plan_hash)return;
 const allowed=saved.allowed_actions||[];
 if(allowed.includes('cancel-stop')){
  if(!window.confirm('SELL STEP 1: cancel the existing SL first. No separate SELL will be sent now. Verify the SL is inactive, then press SELL again.'))return;
  await action({action:'cancel-stop',confirm:saved.plan_hash},'Cancel SL before SELL');
  return;
 }
 if(allowed.includes('close')){
  if(!window.confirm('REAL LIMIT SELL: close only the verified remaining test long at '+rupees(saved.plan_spec?.sell_limit_price)+'. The order may remain unfilled. Submit exactly one SELL?'))return;
  await action({action:'close',confirm:saved.plan_hash},'SELL');return;
 }
 message('SELL is unavailable until the broker confirms a fully filled owned long and that SL is inactive. Refresh broker status.','error');
}
async function clickRefresh(){
 const allowed=saved.allowed_actions||[];
 await action({action:allowed.includes('capture')?'capture':'status'},'Read broker');
}
function contractChanged(){
 verified=null;for(const id of ['expiry','qty','tick'])el(id).value='';
 el('contract').textContent='Symbol changed. Load the exact contract to continue.';
 message('Choose the listed contract before BUY. No order sent.');
 render();
}
el('index').addEventListener('change',contractChanged);
el('symbol').addEventListener('input',contractChanged);
el('load').addEventListener('click',loadContract);
el('buy').addEventListener('click',clickBuy);
el('set-sl').addEventListener('click',clickSL);
el('sell').addEventListener('click',clickSell);
el('refresh').addEventListener('click',clickRefresh);
el('cancel').addEventListener('click',async()=>{
 if(window.confirm('Cancel only the original pending test order? No new BUY or SELL will be sent.'))
  await action({action:'cancel',confirm:saved.plan_hash},'Cancel original order');
});
el('retire').addEventListener('click',async()=>{
 if(window.confirm('Reconcile only a cancelled or closed test with Groww (no new order)?'))
  await action({action:'retire'},'Reconcile test');
});
initial();