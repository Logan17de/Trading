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
let runtimeTimer = null;
let directSupported = false;
let verifiedContract = null;
const nextSteps = {
  NOT_STARTED:'Load your contract, enter the prices and review your test.',
  PRIOR_TEST_RETIRED:'The old test is archived. Your last draft is kept for editing.',
  OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT:'Review the details below. Your Buy click sends the real order.',
  OWNER_TEST_RUNNING:'Oracle is running your reviewed test. You can close this page.',
  BUY_PENDING_OR_PARTIAL:'The limit BUY has not filled completely. Oracle is following the original order.',
  CHILD_FILLED:'BUY filled. The stop-loss is not yet confirmed. Follow the protection status here.',
  STOP_ACTIVE_OBSERVED:'Stop-loss accepted. Oracle is checking persistence and monitoring your target.',
  CLOSE_PENDING_OR_PARTIAL:'Exit is pending or partially filled. The position is not confirmed closed.',
  FLAT_AWAITING_SECOND_READ:'The position is flat. Oracle is waiting for a second independent check.',
  CANCELLED_FLAT:'The order ended without a completed trade. There is no remaining test position.',
  CLOSED_UNVERIFIED:'The test position is closed. BUY, protection and exit checks are recorded; a generated stop-order fill has not been verified.',
  COMPLETE:'The broker checks are complete. Live preparation remains your separate action.',
  RECONCILIATION_REQUIRED:'Oracle cannot confirm the broker state. Check Groww before another order.'
};
const money = value => value != null && Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(Number(value)) : '—';
const words = value => String(value || '').replaceAll('_',' ').toLowerCase().replace(/^./,s=>s.toUpperCase());
const reasons = {
  TURN_ALGO_OFF_BEFORE_SETUP:'Oracle requires global Algo Off for real trade preview. Turn Algo Off in Options Trader, then retry. Contract lookup remains read-only.',
  COMMISSIONING_PRICE_NOT_ON_TICK:'Each price must be a multiple of the contract’s tick size.',
  COMMISSIONING_BUY_LIMIT_BELOW_TRIGGER:'BUY limit must be at least the BUY trigger.',
  COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG:'The test window expired, the contract already expired, or an expiry-day LIMIT test is outside the early-session safety cutoff (BUY by 13:00 IST, review before 12:30 IST). GTT BUY requires a later expiry.',
  COMMISSIONING_TRIGGER_MUST_START_ABOVE_MARKET:'This GTT test waits for an upward crossing. The current ask is already at or above your trigger. Edit the unsubmitted preview; prices are never changed for you.',
  COMMISSIONING_STOP_PRICE_ORDER_INVALID:'Stop limit must be at or below the stop trigger; both must be below the BUY trigger.',
  COMMISSIONING_CANCEL_STOP_BEFORE_CLOSE:'Cancel and verify the stop-loss before sending a separate SELL.',
  COMMISSIONING_STOP_MUST_START_BELOW_MARKET:'The declared stop is already at or above the current bid. No stop was submitted; manage the test position in Groww or use its explicit close action.',
  COMMISSIONING_PARENT_READBACK_DIFFERS:'Groww accepted an instruction, but its returned details could not be verified. Check the existing instruction in Groww. Do not submit another test.',
  COMMISSIONING_CREATE_RECONCILIATION_REQUIRED:'Submission outcome needs verification. Read the original broker evidence; do not repeat the BUY.'
  ,REVIEW_EXISTING_PLAN_ON_ORACLE:'The saved test belongs to an earlier Oracle release. Use Reconcile cancelled test if it was cancelled and is flat. Your draft stays here.',
  EXISTING_PLAN_REQUIRES_REVIEW:'A previous test still needs reconciliation. Read broker evidence or reconcile the cancelled test before reviewing another.',
  COMMISSIONING_DIRECT_TARGET_MUST_EXCEED_BUY_LIMIT:'The target must be above the BUY limit for this long-option test.'
  ,COMMISSIONING_PARTIAL_BUY_REQUIRES_REVIEW:'The BUY filled only partly. Automatic continuation stopped; this quantity has no confirmed stop. Check the original order and position in Groww.',
  OWNER_TEST_JOB_START_FAILED:'Oracle could not confirm the test job started. Continuation is stopped. Read the saved status before trying another test.'
};
const labels = {oracle_order_write:'BUY accepted',persistent_gtt:'GTT persisted',exact_child_link:'Generated GTT order linked',child_filled:'BUY filled',standard_order_write:'Exit order linked',standard_order_filled:'SELL filled',stop_order_write:'Stop-loss accepted',stop_persisted:'Stop-loss persisted',stop_inactive:'Stop-loss settled',flat_confirmed:'Flat position confirmed',inactive_parent:'Test instructions inactive'};
function previewFeedback(message, kind='info', focus=false) {
  const notice=$('#preview-feedback');notice.textContent=message;notice.dataset.kind=kind;notice.hidden=false;
  if(focus){notice.focus({preventScroll:true});notice.scrollIntoView({block:'nearest'});}
}
function render() {
  const allowed = new Set(current.allowed_actions || []);
  $('#setup-state').textContent = words(current.status || 'SETUP_STATUS_UNKNOWN');
  $('#setup-reason').textContent = reasons[current.reason] || words(current.reason || current.pending_action || '');
  $('#next-action').textContent = nextSteps[current.status] || 'Check the latest broker status before taking another action.';
  $('#plan-fields').disabled = busy || !allowed.has('preview');
  $('#preview-button').disabled = busy || !allowed.has('preview');
  $('#preview-button').textContent = busy && activeAction==='preview' ? 'Checking preview…' : 'Review test · no order';
  $('#refresh-status').disabled = busy;
  $('#lookup-contract').disabled = busy || !allowed.has('preview');
  for (const button of document.querySelectorAll('[data-read]')) {
    button.hidden = !allowed.has(button.dataset.read); button.disabled = busy;
  }
  let writes = false;
  for (const button of document.querySelectorAll('[data-write]')) {
    const action = button.dataset.write;
    button.hidden = !allowed.has(action); writes ||= !button.hidden;
    button.disabled = busy || savedOnly || (action!=='buy-protect' && !$('#confirm-action').checked) || !(action==='arm' ? current.evidence_digest : current.plan_hash);
  }
  $('#write-actions').hidden = !writes;
  $('#confirm-row').hidden = allowed.has('buy-protect');
  $('#confirm-label').textContent = allowed.has('arm') ? 'I reviewed the verified test. Prepare real trading with Algo Off; I will start it separately.' : 'I reviewed the exact contract, quantity and prices above. The selected action sends a real broker request.';
  const spec = current.plan_spec;
  $('#review-plan').replaceChildren(); $('#review-plan').hidden = !spec; $('#test-effect').hidden = !spec;
  if (spec) {
    const rows = [['Contract',spec.contract.symbol,'wide'],['Quantity',`${spec.quantity} · one lot`],['Expiry',spec.contract.expiry],...(spec.entry_mode==='LIMIT'?[]:[['BUY trigger',money(spec.trigger_price)]]),['BUY limit',money(spec.buy_limit_price)],['Target / SELL limit',money(spec.sell_limit_price)],['Stop-loss trigger',money(spec.stop_trigger_price)],['Stop-loss limit',money(spec.stop_limit_price)],['Maximum purchase debit before charges',money(spec.quantity*spec.buy_limit_price)],['Submit BUY before',new Date(spec.valid_until).toLocaleString(),'wide']];
    for (const [label,value,kind] of rows) { const div=document.createElement('div');if(kind)div.className=kind;const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;div.append(dt,dd);$('#review-plan').append(div); }
  }
  $('#test-effect').textContent = spec?.entry_mode==='LIMIT' ? 'Buy + protect authorizes one real limit BUY, stop-loss after the full fill, and an Oracle-managed target exit. The BUY and stop are separate requests. Failed or uncertain protection needs attention; prices are never raised to force a fill.' : 'This GTT BUY waits for its price trigger. After a fill, Attach stop-loss is a separate action. Acceptance does not mean filled.';
  if(spec?.entry_mode==='LIMIT' && spec.contract.expiry===new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())){
    $('#test-effect').textContent += ' EXPIRY TODAY: You can lose the entire paid premium and charges. The stop/target may be rejected or remain unfilled, and the option can expire before exit. No automatic exit is guaranteed.';
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
  verifiedContract = null; // A saved draft is not a fresh contract lookup.
  if(!spec)return;
  const values = {...spec,...spec.contract,lot_size:spec.quantity};
  for(const key of ['index','symbol','expiry','lot_size','tick_size','trigger_price','buy_limit_price','sell_limit_price','stop_trigger_price','stop_limit_price','egress_ip']) {
    const input = $(`[name="${key}"]`); if(input && values[key]!=null)input.value=values[key];
  }
  $('[name="entry_mode"]').value=spec.entry_mode || 'GTT';entryMode();
}
function entryMode(){
  const direct=$('[name="entry_mode"]').value==='LIMIT';
  $('#buy-trigger-field').hidden=direct;$('[name="trigger_price"]').required=!direct;
  $('#entry-explanation').textContent=direct ? 'Oracle sends your limit BUY, then attaches the declared stop after a full fill. It monitors the target and verifies stop cancellation before selling. A limit order can remain unfilled.' : 'GTT waits for an upward price crossing. Its BUY trigger must be above the current ask. Each subsequent broker action requires your click.';
}
function invalidateContract(){
  verifiedContract=null;
  for(const key of ['expiry','lot_size','tick_size'])document.querySelector('[name="'+key+'"]').value='';
  // Oracle egress IPv4 is not contract metadata. Keep the owner's entered value
  // when switching symbols; Oracle independently verifies it at real preview.
  document.querySelector('#contract-summary').textContent='Contract details require a fresh lookup for this exact symbol. Prices were not changed.';
}
async function lookupContract(){
  if(busy)return;
  const requested={index:document.querySelector('[name="index"]').value,symbol:document.querySelector('[name="symbol"]').value.trim()};
  busy=true;activeAction='lookup';render();
  $('#contract-summary').textContent='Reading the contract and current quote from Oracle…';
  try{
    const response=await fetch('/api/live-setup',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({action:'lookup',spec:requested}),cache:'no-store'});
    const data=await response.json();
    if(data.status!=='OWNER_CONTRACT_FOUND')throw new Error(reasons[data.reason] || words(data.reason || data.status));
    if(data.contract.index!==requested.index || data.contract.symbol!==requested.symbol)throw new Error('Oracle returned a different contract. No details applied');
    for(const key of ['expiry','lot_size','tick_size'])document.querySelector('[name="'+key+'"]').value=data.contract[key];
    if(data.egress_ip)document.querySelector('[name="egress_ip"]').value=data.egress_ip;
    const publicOnly=data.source==='GROWW_PUBLIC_INSTRUMENT_MASTER';
    verifiedContract={...data.contract,egress_ip:publicOnly?null:data.egress_ip};
    previewFeedback(publicOnly ? 'Contract details loaded from Groww public master. Live quote unavailable while Algo On. Check expiry and one-lot quantity, then Review; real preview still requires Algo Off.' : 'Contract and broker quote loaded. Review the expiry and one-lot quantity before proceeding.','info');
    const q=data.quote || {};
    $('#contract-summary').textContent=publicOnly ?
      `${data.contract.index} · ${data.contract.expiry} · 1 lot = ${data.contract.lot_size} · Tick ${money(data.contract.tick_size)}. Contract found in Groww public master. Live quote unavailable because Oracle requires Algo Off. Enter/confirm Oracle IPv4; real preview verifies quote, account, and IP. No order sent.` :
      `${data.contract.index} · ${data.contract.expiry} · 1 lot = ${data.contract.lot_size} · Tick ${money(data.contract.tick_size)}. Last ${money(q.last_price)} · Bid ${money(q.bid_price)} · Ask ${money(q.offer_price)}. ${q.timestamp_status==='RECENT_TRADE_ONLY'?'Quote received':'Quote not current'} ${new Date(data.checked_at).toLocaleTimeString()}.`;
  }catch(error){
    invalidateContract();
    document.querySelector('#contract-summary').textContent='Contract lookup: '+error.message+'. Retry after resolving the cause. Your prices are unchanged.';
    previewFeedback('Contract lookup blocked: '+error.message+'. No order sent.','error',true);
  }
  finally{busy=false;render();}
}
async function loadSaved() {
  try {
    const response=await fetch('/api/live-setup',{cache:'no-store'});
    const result=await response.json();current=result;savedOnly=true;restoreForm(current.plan_spec || current.draft_spec);
    if(!current.plan_spec){$('[name="entry_mode"]').value='LIMIT';entryMode();}
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
    // Retirement archives broker evidence. Keep the owner's form and prices.
    const elapsed=((Date.now()-startedAt)/1000).toFixed(1);
    const note = result.broker_write_outcome==='ACCEPTED' ? 'Broker accepted the request. Acceptance does not mean filled. Read broker evidence for current state.' : result.broker_write_outcome==='UNCERTAIN' ? 'The broker result is uncertain. Refresh the saved test; do not submit another order.' :
      result.reason ? 'The check is blocked. Review the reason above.' : result.status==='OWNER_TEST_RUNNING' ? 'Your test job started on Oracle. Waiting for its actual BUY and protection results.' : result.status==='NOT_STARTED' ? 'Load the contract, enter your prices and review the test.' : 'Setup check completed.';
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
$('#preview-button').addEventListener('click',event=>{
  // Native HTML validation can prevent submit when a changed symbol cleared the
  // old expiry/lot. Trigger a read-only contract lookup instead of a dead-end warning.
  const index=$('[name="index"]').value, symbol=$('[name="symbol"]').value.trim();
  if(!busy && (!verifiedContract || verifiedContract.index!==index || verifiedContract.symbol!==symbol)){
    event.preventDefault();
    previewFeedback('Loading the listed contract first. Review the populated details and click Review again. No order sent.');
    lookupContract();
  }
});
$('#plan-form').addEventListener('submit',event=>{
  event.preventDefault();if(busy || !(current.allowed_actions || []).includes('preview'))return;
  const fields=new FormData(event.currentTarget);const value=key=>Number(fields.get(key));const now=new Date();
  const direct=fields.get('entry_mode')==='LIMIT';
  if(direct && !directSupported){previewFeedback('The installed Oracle release does not support the new limit test yet. No order was sent.','error',true);return;}
  if(!verifiedContract || verifiedContract.index!==fields.get('index') || verifiedContract.symbol!==fields.get('symbol').trim() || verifiedContract.expiry!==fields.get('expiry') || verifiedContract.lot_size!==value('lot_size') || Number(verifiedContract.tick_size)!==value('tick_size') || (verifiedContract.egress_ip!==null && verifiedContract.egress_ip!==fields.get('egress_ip'))){
    previewFeedback('Load contract & current price for this exact symbol before reviewing the test. Old expiry and lot details cannot be reused. No order was sent.','error',true);return;
  }
  const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
  if(fields.get('expiry')<today || (fields.get('expiry')===today && !direct)){
    $('#setup-state').textContent='Contract expiry is not eligible';
    $('#setup-reason').textContent='Expired contracts cannot be used. An expiry-day test must be an explicit protected LIMIT BUY, not GTT.';
    $('#command-note').textContent='Your draft is unchanged. Contract expiry is populated by exact broker lookup.';
    previewFeedback('Preview blocked: this option has expired, or expiry-day GTT entry is unsupported. No order was sent. Load the exact contract to continue.','error',true);
    return;
  }
  command({action:'preview',spec:{...(direct?{entry_mode:'LIMIT'}:{}),contract:{symbol:fields.get('symbol').trim(),index:fields.get('index'),expiry:fields.get('expiry'),lot_size:value('lot_size'),tick_size:value('tick_size')},quantity:value('lot_size'),trigger_price:direct?value('buy_limit_price'):value('trigger_price'),buy_limit_price:value('buy_limit_price'),sell_limit_price:value('sell_limit_price'),stop_trigger_price:value('stop_trigger_price'),stop_limit_price:value('stop_limit_price'),valid_until:new Date(now.getTime()+30*60*1000).toISOString(),egress_ip:fields.get('egress_ip').trim()}});
});
$('#plan-form').addEventListener('invalid',event=>{
  $('#technical-details').open=true;
  previewFeedback(`Check ${event.target.labels?.[0]?.textContent.trim() || 'the highlighted field'}: ${event.target.validationMessage}`,'error');
},true);
function contractFieldChanged(event){
  if(event.target.matches('[name="symbol"],[name="index"]'))invalidateContract();
  else if(['expiry','lot_size','tick_size'].includes(event.target.name) ||
          (event.target.name==='egress_ip' && verifiedContract?.egress_ip!=null))verifiedContract=null;
  // Public master lookup has no verified egress IPv4. Allow the owner to enter
  // it afterward; Oracle independently validates it during real preview.
  document.querySelector('#preview-feedback').hidden=true;
}
document.querySelector('#plan-form').addEventListener('input',contractFieldChanged);
document.querySelector('#plan-form').addEventListener('change',contractFieldChanged);
$('#refresh-status').addEventListener('click',()=>command({action:'status'}));
$('#lookup-contract').addEventListener('click',lookupContract);
$('[name="entry_mode"]').addEventListener('change',entryMode);
$('#confirm-action').addEventListener('change',render);
for(const button of document.querySelectorAll('[data-read]'))button.addEventListener('click',()=>command({action:button.dataset.read}));
for(const button of document.querySelectorAll('[data-write]'))button.addEventListener('click',()=>{
  const action=button.dataset.write;
  if(busy || savedOnly || (action!=='buy-protect' && !$('#confirm-action').checked) || !(current.allowed_actions||[]).includes(action))return;
  if(action==='buy-protect' && current.plan_spec?.contract?.expiry===new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())){
    if(!window.confirm('This contract expires TODAY. A real one-lot BUY can lose its full paid premium and charges. The subsequent stop or target may not be accepted or filled. Continue with the exact reviewed real order?'))return;
  }
  command({action,confirm:action==='arm'?current.evidence_digest:current.plan_hash});
});
function readRuntime(){return fetch('/api/dashboard',{cache:'no-store'}).then(r=>r.json()).then(data=>{
  const setup=data.live_setup;
  directSupported=setup?.supports_direct_test===true;
  const on=data.control?.algo?.desired_enabled;
  const mode=setup?.mode==='live'?'Live mode':setup?.mode==='paper'?'Paper mode':'Mode unknown';
  $('#runtime-status').textContent = setup ? `${mode} · ${on===true?'Algo On':on===false?'Algo Off':'Algo state unknown'}` : 'Setup status unavailable';
  if(!busy && current.plan_hash && setup?.commissioning?.plan_hash===current.plan_hash && setup.commissioning.binding_current===true){
    const result=setup.commissioning;
    current={...current,status:result.status,facts:result.facts,reason:result.reason};
    $('#command-note').textContent=`Oracle test observation: ${result.checked_at?new Date(result.checked_at).toLocaleTimeString():'waiting'}. ${data.freshness==='RECENT'?'Recent viewer data.':'Viewer data is stale; check Groww.'}`;
    render();
  }
  // Pre-submission failures have no commissioning record yet. Match the exact
  // authorization independently so the page cannot keep saying Running forever.
  if(!busy && current.plan_hash && setup?.owner_test?.plan_hash===current.plan_hash && setup.owner_test.state==='ERROR'){
    current={...current,status:'OWNER_TEST_FAILED',reason:setup.owner_test.reason};
    $('#command-note').textContent='Oracle stopped this test. Check the reported reason and Groww before another action.';render();
  }
  if(!busy && current.plan_hash && setup?.owner_test?.plan_hash===current.plan_hash && setup.owner_test.attention){
    $('#command-note').textContent='Target SELL is still pending and the stop is inactive. The position remains open. Check Groww; Oracle will not send a duplicate SELL.';
  }
}).catch(()=>{$('#runtime-status').textContent='Oracle status unavailable';}).finally(()=>{
  clearTimeout(runtimeTimer);runtimeTimer=setTimeout(readRuntime,3000);
});}
loadSaved();
readRuntime();
