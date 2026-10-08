/* Owner actions only. Never retry broker writes or activate from a page load. */
'use strict';
const $ = selector => document.querySelector(selector);
const token = $('meta[name="local-token"]').content;
let current = {allowed_actions:[]};
let busy = false;
let savedOnly = true;
let startedAt = 0;
let activeAction = '';
let progressTimer = null;
const money = value => value != null && Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(Number(value)) : '—';
const words = value => String(value || '').replaceAll('_',' ').toLowerCase().replace(/^./,s=>s.toUpperCase());
const reasons = {
  COMMISSIONING_PRICE_NOT_ON_TICK:'Each price must be a multiple of the contract’s tick size.',
  COMMISSIONING_BUY_LIMIT_BELOW_TRIGGER:'BUY limit must be at least the BUY trigger.',
  COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG:'The BUY submission window expired, or the contract expires today or earlier. A new preview renews an unsubmitted window; this test requires a later contract expiry.',
  COMMISSIONING_TRIGGER_MUST_START_ABOVE_MARKET:'This GTT test waits for an upward crossing. The current ask is already at or above your trigger. Edit the unsubmitted preview; prices are never changed for you.',
  COMMISSIONING_STOP_PRICE_ORDER_INVALID:'Stop limit must be at or below the stop trigger; both must be below the BUY trigger.',
  COMMISSIONING_CANCEL_STOP_BEFORE_CLOSE:'Cancel and verify the stop-loss before sending a separate SELL.',
  COMMISSIONING_STOP_MUST_START_BELOW_MARKET:'The declared stop is already at or above the current bid. No stop was submitted; manage the test position in Groww or use its explicit close action.',
  COMMISSIONING_PARENT_READBACK_DIFFERS:'Groww accepted an instruction, but its returned details could not be verified. Check the existing instruction in Groww. Do not submit another test.',
  COMMISSIONING_CREATE_RECONCILIATION_REQUIRED:'Submission outcome needs verification. Read the original broker evidence; do not repeat the BUY.'
};
const labels = {oracle_order_write:'Oracle order accepted',persistent_gtt:'GTT persisted',exact_child_link:'Exact generated order',child_filled:'Test BUY filled',standard_order_write:'Exit order linked',standard_order_filled:'Test SELL filled',stop_order_write:'Stop-loss accepted',stop_persisted:'Stop-loss persisted',stop_inactive:'Stop-loss settled',flat_confirmed:'Flat position confirmed',inactive_parent:'GTT inactive'};
function previewFeedback(message, kind='info', focus=false) {
  const notice=$('#preview-feedback');notice.textContent=message;notice.dataset.kind=kind;notice.hidden=false;
  if(focus){notice.focus({preventScroll:true});notice.scrollIntoView({block:'nearest'});}
}
function render() {
  const allowed = new Set(current.allowed_actions || []);
  $('#setup-state').textContent = words(current.status || 'SETUP_STATUS_UNKNOWN');
  $('#setup-reason').textContent = reasons[current.reason] || words(current.reason || current.pending_action || '');
  $('#plan-fields').disabled = busy || !allowed.has('preview');
  $('#preview-button').disabled = busy || !allowed.has('preview');
  $('#preview-button').textContent = busy && activeAction==='preview' ? 'Checking preview…' : 'Preview · no order';
  $('#refresh-status').disabled = busy;
  for (const button of document.querySelectorAll('[data-read]')) {
    button.hidden = !allowed.has(button.dataset.read); button.disabled = busy;
  }
  let writes = false;
  for (const button of document.querySelectorAll('[data-write]')) {
    const action = button.dataset.write;
    button.hidden = !allowed.has(action); writes ||= !button.hidden;
    button.disabled = busy || savedOnly || !$('#confirm-action').checked || !(action==='arm' ? current.evidence_digest : current.plan_hash);
  }
  $('#write-actions').hidden = !writes;
  $('#confirm-label').textContent = allowed.has('arm') ? 'I reviewed the verified test. Prepare real trading with Algo Off; I will start it separately.' : 'I reviewed the exact contract, quantity and prices above. The selected action sends a real broker request.';
  const spec = current.plan_spec;
  $('#review-plan').replaceChildren(); $('#review-plan').hidden = !spec; $('#test-effect').hidden = !spec;
  if (spec) {
    const rows = [['Contract',spec.contract.symbol,'wide'],['Quantity',`${spec.quantity} · one lot`],['Expiry',spec.contract.expiry],['BUY trigger',money(spec.trigger_price)],['BUY limit',money(spec.buy_limit_price)],['Close SELL limit',money(spec.sell_limit_price)],['Stop-loss trigger',money(spec.stop_trigger_price)],['Stop-loss limit',money(spec.stop_limit_price)],['Maximum purchase debit',money(spec.quantity*spec.buy_limit_price)],['Submit BUY before',new Date(spec.valid_until).toLocaleString(),'wide']];
    for (const [label,value,kind] of rows) { const div=document.createElement('div');if(kind)div.className=kind;const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;div.append(dt,dd);$('#review-plan').append(div); }
  }
  $('#evidence').replaceChildren();
  for (const [key,label] of Object.entries(labels)) {const span=document.createElement('span');const ok=current.facts?.[key]===true;span.textContent=`${ok?'✓':'○'} ${label}`;if(ok)span.className='positive';$('#evidence').append(span);}
}
function progress() {
  const seconds = Math.max(0,Math.floor((Date.now()-startedAt)/1000));
  const detail = activeAction==='arm' ? 'Replay checks, backup and live preparation can take several minutes.' :
    ['preview','submit'].includes(activeAction) ? 'Oracle is checking broker state and active GTT history. This can take time.' :
    'Oracle is authenticating and checking the saved test.';
  $('#command-note').textContent = `${words(activeAction)} · ${seconds}s elapsed. ${detail} This request will not be resent automatically.`;
  if(activeAction==='preview')previewFeedback(`Checking preview · ${seconds}s. Oracle is checking the contract and account. No order is being submitted.`);
}
function restoreForm(spec) {
  if(!spec)return;
  const values = {...spec,...spec.contract,lot_size:spec.quantity};
  for(const key of ['index','symbol','expiry','lot_size','tick_size','trigger_price','buy_limit_price','sell_limit_price','stop_trigger_price','stop_limit_price','egress_ip']) {
    const input = $(`[name="${key}"]`); if(input && values[key]!=null)input.value=values[key];
  }
}
async function loadSaved() {
  try {
    const response=await fetch('/api/live-setup',{cache:'no-store'});
    const result=await response.json();current=result;savedOnly=true;restoreForm(current.plan_spec);
    if(result.operation) {
      busy=true;activeAction=result.operation.action;startedAt=Date.now()-result.operation.elapsed_seconds*1000;progress();
      setTimeout(loadSaved,1000);
    } else {
      busy=false;
      $('#command-note').textContent='Saved setup loaded from this PC. Refresh status or read broker evidence for a current check. No broker request was sent by opening this page.';
    }
  } catch {
    current={status:'OWNER_SETUP_LOCAL_STATE_UNAVAILABLE',allowed_actions:['status']};busy=false;
    $('#command-note').textContent='Saved status is unavailable. Refresh the existing setup; do not repeat an order.';
  }
  render();
}
async function command(body) {
  if(busy)return;
  activeAction=body.action;
  busy=true;$('#confirm-action').checked=false;render();
  activeAction=body.action;startedAt=Date.now();progress();progressTimer=setInterval(progress,1000);
  try {
    const response=await fetch('/api/live-setup',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body),cache:'no-store'});
    const result=await response.json(); current=result;savedOnly=false;
    if(result.status==='PRIOR_TEST_RETIRED')$('#plan-form').reset();
    const elapsed=((Date.now()-startedAt)/1000).toFixed(1);
    const note = result.broker_write_outcome==='ACCEPTED' ? 'Broker accepted the request. Acceptance does not mean filled. Read broker evidence for current state.' : result.broker_write_outcome==='UNCERTAIN' ? 'The broker result is uncertain. Refresh the saved test; do not submit another order.' :
      result.reason ? 'The check is blocked. Review the reason above.' : result.status==='NOT_STARTED' ? 'No test has been previewed. Complete the form and click Preview · no order.' : 'Setup check completed.';
    $('#command-note').textContent = `${note} ${elapsed}s.`;
    if(body.action==='preview'){
      const ready=result.status==='OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT';
      previewFeedback(ready ? 'Preview ready. Review the contract and prices in Broker verification. No order has been placed.' :
        `Preview: ${reasons[result.reason] || words(result.reason || result.status)}. Check Broker verification for details.`,ready?'success':'error',true);
    }
  } catch {
    current={...current,status:'SETUP_RESULT_UNKNOWN',reason:'Refresh the existing setup before any further action.',allowed_actions:['status']};
    $('#command-note').textContent='Connection interrupted. An order may have reached the broker. No automatic retry was sent.';
    if(body.action==='preview')previewFeedback('Preview response was interrupted. Refresh the saved setup before trying again. No automatic retry was sent.','error',true);
  } finally {clearInterval(progressTimer);progressTimer=null;busy=false;render();readRuntime();}
}
$('#plan-form').addEventListener('submit',event=>{
  event.preventDefault();if(busy || !(current.allowed_actions || []).includes('preview'))return;
  const fields=new FormData(event.currentTarget);const value=key=>Number(fields.get(key));const now=new Date();
  const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
  if(fields.get('expiry')<=today){
    $('#setup-state').textContent='Contract expiry is not eligible';
    $('#setup-reason').textContent='This broker test currently requires an option expiring after today. No request was sent to Oracle or Groww.';
    $('#command-note').textContent='Your draft is unchanged. The option expiry identifies the contract; the BUY submission window is set automatically.';
    previewFeedback(`Preview blocked: the selected contract expires ${fields.get('expiry')}. This test requires an expiry after today. No order was sent. Changing the date alone would not change the contract.`,'error',true);
    return;
  }
  command({action:'preview',spec:{contract:{symbol:fields.get('symbol').trim(),index:fields.get('index'),expiry:fields.get('expiry'),lot_size:value('lot_size'),tick_size:value('tick_size')},quantity:value('lot_size'),trigger_price:value('trigger_price'),buy_limit_price:value('buy_limit_price'),sell_limit_price:value('sell_limit_price'),stop_trigger_price:value('stop_trigger_price'),stop_limit_price:value('stop_limit_price'),valid_until:new Date(now.getTime()+30*60*1000).toISOString(),egress_ip:fields.get('egress_ip').trim()}});
});
$('#plan-form').addEventListener('invalid',event=>{
  previewFeedback(`Check ${event.target.labels?.[0]?.textContent.trim() || 'the highlighted field'}: ${event.target.validationMessage}`,'error');
},true);
$('#plan-form').addEventListener('input',()=>{$('#preview-feedback').hidden=true;});
$('#refresh-status').addEventListener('click',()=>command({action:'status'}));
$('#confirm-action').addEventListener('change',render);
for(const button of document.querySelectorAll('[data-read]'))button.addEventListener('click',()=>command({action:button.dataset.read}));
for(const button of document.querySelectorAll('[data-write]'))button.addEventListener('click',()=>{if(busy || savedOnly || !$('#confirm-action').checked)return;const action=button.dataset.write;command({action,confirm:action==='arm'?current.evidence_digest:current.plan_hash});});
function readRuntime(){return fetch('/api/dashboard',{cache:'no-store'}).then(r=>r.json()).then(data=>{
  const setup=data.live_setup;
  const on=data.control?.algo?.desired_enabled;
  const mode=setup?.mode==='live'?'Live mode':setup?.mode==='paper'?'Paper mode':'Mode unknown';
  $('#runtime-status').textContent = setup ? `${mode} · ${on===true?'Algo On':on===false?'Algo Off':'Algo state unknown'}` : 'Setup status unavailable';
}).catch(()=>{$('#runtime-status').textContent='Oracle status unavailable';});}
loadSaved();
readRuntime();
