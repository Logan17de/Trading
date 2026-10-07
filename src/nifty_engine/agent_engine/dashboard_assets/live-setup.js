/* Owner actions only. Never retry broker writes or activate from a page load. */
'use strict';
const $ = selector => document.querySelector(selector);
const token = $('meta[name="local-token"]').content;
let current = {allowed_actions:[]};
let busy = false;
const money = value => value != null && Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(Number(value)) : '—';
const words = value => String(value || '').replaceAll('_',' ').toLowerCase().replace(/^./,s=>s.toUpperCase());
const labels = {oracle_order_write:'Oracle order accepted',persistent_gtt:'GTT persisted',exact_child_link:'Exact generated order',child_filled:'Test BUY filled',standard_order_write:'Close order accepted',standard_order_filled:'Test SELL filled',flat_confirmed:'Flat position confirmed',inactive_parent:'GTT inactive'};
function render() {
  const allowed = new Set(current.allowed_actions || []);
  $('#setup-state').textContent = words(current.status || 'SETUP_STATUS_UNKNOWN');
  $('#setup-reason').textContent = words(current.reason || current.pending_action || '');
  $('#plan-fields').disabled = busy || !allowed.has('preview');
  $('#preview-button').disabled = busy || !allowed.has('preview');
  $('#refresh-status').disabled = busy;
  for (const button of document.querySelectorAll('[data-read]')) {
    button.hidden = !allowed.has(button.dataset.read); button.disabled = busy;
  }
  let writes = false;
  for (const button of document.querySelectorAll('[data-write]')) {
    const action = button.dataset.write;
    button.hidden = !allowed.has(action); writes ||= !button.hidden;
    button.disabled = busy || !$('#confirm-action').checked || !(action==='arm' ? current.evidence_digest : current.plan_hash);
  }
  $('#write-actions').hidden = !writes;
  $('#confirm-label').textContent = allowed.has('arm') ? 'I reviewed the verified test. Prepare real trading with Algo Off; I will start it separately.' : 'I reviewed the exact contract, quantity and prices above. The selected action sends a real broker request.';
  const spec = current.plan_spec;
  $('#review-plan').replaceChildren(); $('#review-plan').hidden = !spec; $('#test-effect').hidden = !spec;
  if (spec) {
    const rows = [['Contract',spec.contract.symbol,'wide'],['Quantity',`${spec.quantity} · one lot`],['Expiry',spec.contract.expiry],['BUY trigger',money(spec.trigger_price)],['BUY limit',money(spec.buy_limit_price)],['Close SELL limit',money(spec.sell_limit_price)],['Maximum purchase debit',money(spec.quantity*spec.buy_limit_price)],['Plan expires',new Date(spec.valid_until).toLocaleString(),'wide']];
    for (const [label,value,kind] of rows) { const div=document.createElement('div');if(kind)div.className=kind;const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;div.append(dt,dd);$('#review-plan').append(div); }
  }
  $('#evidence').replaceChildren();
  for (const [key,label] of Object.entries(labels)) {const span=document.createElement('span');const ok=current.facts?.[key]===true;span.textContent=`${ok?'✓':'○'} ${label}`;if(ok)span.className='positive';$('#evidence').append(span);}
}
async function command(body) {
  if(busy)return;
  busy=true;$('#confirm-action').checked=false;render();
  $('#command-note').textContent=body.action==='arm'?'Preparing live mode can take several minutes. Algo remains Off. Keep this page open.':'Waiting for Oracle. This request will not be retried automatically.';
  try {
    const response=await fetch('/api/live-setup',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body),cache:'no-store'});
    const result=await response.json(); current=result;
    $('#command-note').textContent = result.broker_write_outcome==='ACCEPTED' ? 'Broker accepted the request. Read broker evidence to verify its actual state and fills.' : result.broker_write_outcome==='UNCERTAIN' ? 'The broker result is uncertain. Refresh the saved test; do not submit another order.' : 'Setup state saved. Choose the next action when you are ready.';
  } catch {
    current={...current,status:'SETUP_RESULT_UNKNOWN',reason:'Refresh the existing setup before any further action.',allowed_actions:['status']};
    $('#command-note').textContent='Connection interrupted. An order may have reached the broker. No automatic retry was sent.';
  } finally {busy=false;render();readRuntime();}
}
$('#plan-form').addEventListener('submit',event=>{
  event.preventDefault();const fields=new FormData(event.currentTarget);const value=key=>Number(fields.get(key));
  command({action:'preview',spec:{contract:{symbol:fields.get('symbol').trim(),index:fields.get('index'),expiry:fields.get('expiry'),lot_size:value('lot_size'),tick_size:value('tick_size')},quantity:value('lot_size'),trigger_price:value('trigger_price'),buy_limit_price:value('buy_limit_price'),sell_limit_price:value('sell_limit_price'),valid_until:new Date(fields.get('valid_until')).toISOString(),egress_ip:fields.get('egress_ip').trim()}});
});
$('#refresh-status').addEventListener('click',()=>command({action:'status'}));
$('#confirm-action').addEventListener('change',render);
for(const button of document.querySelectorAll('[data-read]'))button.addEventListener('click',()=>command({action:button.dataset.read}));
for(const button of document.querySelectorAll('[data-write]'))button.addEventListener('click',()=>{if(!$('#confirm-action').checked)return;const action=button.dataset.write;command({action,confirm:action==='arm'?current.evidence_digest:current.plan_hash});});
function readRuntime(){return fetch('/api/dashboard',{cache:'no-store'}).then(r=>r.json()).then(data=>{
  const setup=data.live_setup;
  const on=data.control?.algo?.desired_enabled;
  const mode=setup?.mode==='live'?'Live mode':setup?.mode==='paper'?'Paper mode':'Mode unknown';
  $('#runtime-status').textContent = setup ? `${mode} · ${on===true?'Algo On':on===false?'Algo Off':'Algo state unknown'}` : 'Setup status unavailable';
}).catch(()=>{$('#runtime-status').textContent='Oracle status unavailable';});}
command({action:'status'});
