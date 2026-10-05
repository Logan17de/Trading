"use strict";
const $ = id => document.getElementById(id);
const money = new Intl.NumberFormat("en-IN", {style:"currency", currency:"INR", maximumFractionDigits:2});
const numeric = new Intl.NumberFormat("en-IN", {maximumFractionDigits:2, minimumFractionDigits:2});
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const known = value => typeof value === "number" && Number.isFinite(value);
const inr = value => known(value) ? money.format(value) : "—";
const signed = value => known(value) ? (value > 0 ? "+" : "") + money.format(value) : "—";
const percent = value => known(value) ? (value > 0 ? "+" : "") + value.toFixed(1) + "%" : "—";
let liveData = null, data = null, demo = false, selectedIndex = "NIFTY", timeZone = "Asia/Tokyo", autoRefresh = true, chartDetail = "live";
let contracts = {buy:null, sell:null}, refreshPending = false, lastRequest = 0;
let chartRange = 'session';
let algoStartPending=false, algoStartResult=null;
try {selectedIndex = localStorage.getItem("trading-index") || "NIFTY"; timeZone = localStorage.getItem("trading-timezone") || timeZone;} catch {}
if (!["NIFTY","SENSEX","BANKNIFTY"].includes(selectedIndex)) selectedIndex = "NIFTY";
if (!["Asia/Tokyo","Asia/Kolkata"].includes(timeZone)) timeZone = "Asia/Tokyo";
const clock = value => new Intl.DateTimeFormat("en-GB", {timeZone, hour:"2-digit", minute:"2-digit"}).format(new Date(value));
const clockSeconds = value => new Intl.DateTimeFormat("en-GB", {timeZone, hour:"2-digit", minute:"2-digit",second:"2-digit"}).format(new Date(value));
const dateTime = value => new Intl.DateTimeFormat("en-GB", {timeZone, day:"2-digit", month:"short", hour:"2-digit", minute:"2-digit", second:"2-digit"}).format(new Date(value));
function tone(element, value) {element.classList.toggle("positive", known(value) && value >= 0); element.classList.toggle("negative", known(value) && value < 0);}
function setAmount(id, value, withSign=false) {$(id).textContent = withSign ? signed(value) : inr(value); tone($(id), withSign ? value : null);}
function emptyChart(container, title, subtitle="") {
  delete container.dataset.chartSignature;
  container.onpointermove=null; container.onpointerleave=null;
  container.innerHTML = `<div class="empty-chart"><svg class="icon" aria-hidden="true"><use href="#icon-bars"/></svg><strong>${escapeHTML(title)}</strong><small>${escapeHTML(subtitle)}</small></div>`;
}
function chart(container, points, {color="#08b75d", area=false, mini=false, pnl=false, label="Chart",levels=[],fiveMinuteGrid=false,gapSeconds=Infinity,lines=[]}={}) {
  const rows = (points || []).filter(p => known(p.value) && Number.isFinite(Date.parse(p.at)));
  if (!rows.length || (mini && rows.length < 2)) {emptyChart(container, rows.length ? "Waiting for more observations" : "No chart data", "No prices are filled in or simulated."); return;}
  const markers=levels.filter(p=>known(p.price)&&p.price>0&&['ENTRY','SL','TARGET'].includes(p.kind));
  // Match the drawing to CSS pixels so a single wide panel cannot stretch type/strokes.
  const width=Math.max(160,Math.round(container.clientWidth)||640), height=Math.max(60,Math.round(container.clientHeight)||(mini?68:280));
  const premium=container.classList.contains('option-chart');
  const signature=JSON.stringify({rows,color,area,mini,pnl,label,markers,timeZone,fiveMinuteGrid,gapSeconds,width,height,lines});
  if(container.dataset.chartSignature===signature) return;
  container.dataset.chartSignature=signature;
  const left=mini?2:48, right=mini?3:premium?76:14, top=mini?5:28, bottom=mini?5:32;
  const domain=[...rows.map(r=>r.value),...markers.map(p=>p.price),...lines.flatMap(s=>s.points.filter(p=>known(p.value)).map(p=>p.value))];
  let low=Math.min(...domain), high=Math.max(...domain);
  if (pnl) {low=Math.min(low,0); high=Math.max(high,0);}
  let span=high-low || Math.max(Math.abs(high)*.02,1); low-=span*.09; high+=span*.09; if(!pnl) low=Math.max(0,low); span=high-low;
  const first=Date.parse(rows[0].at), last=Date.parse(rows.at(-1).at), duration=last-first||1;
  const inset=0;
  const x=r=>rows.length===1?(width+left-right)/2:left+inset+(Date.parse(r.at)-first)/duration*(width-left-right-2*inset), y=v=>top+(high-v)/span*(height-top-bottom);
  const path=rows.map((r,i)=>`${i&&Date.parse(r.at)-Date.parse(rows[i-1].at)<=gapSeconds*1000?"L":"M"}${x(r).toFixed(2)},${y(r.value).toFixed(2)}`).join(" ");
  const hasGaps=rows.some((r,i)=>i&&Date.parse(r.at)-Date.parse(rows[i-1].at)>gapSeconds*1000);
  const base=pnl?y(0):height-bottom, id="fill-"+container.id;
  const fill=`${path} L${x(rows.at(-1)).toFixed(2)},${base.toFixed(2)} L${x(rows[0]).toFixed(2)},${base.toFixed(2)} Z`;
  let grid="";
  if (!mini) {
    const rawStep=span/4, scale=10**Math.floor(Math.log10(rawStep));
    const step=[1,2,2.5,5,10].find(n=>n*scale>=rawStep)*scale;
    for(let value=Math.ceil(low/step)*step;value<=high;value+=step) {
      const at=y(value), tick=Math.abs(value)>=1000?(value/1000).toFixed(Math.abs(value)>=10000?0:1)+"k":value.toFixed(step<1?2:step<10?1:0);
      grid+=`<line x1="${left}" y1="${at}" x2="${width-right}" y2="${at}" stroke="#e8eeeb" vector-effect="non-scaling-stroke"/><text x="${left-10}" y="${at+4}" text-anchor="end" fill="#526980" font-size="12">${tick}</text>`;
    }
    const tickCount=Math.max(2,Math.floor((width-left-right)/100));
    const minimumMinutes=duration/60000/tickCount;
    const tickStep=([5,10,15,30,60,120,180,240,360,720].find(n=>n>=minimumMinutes)||1440)*60000;
    const alignedStart=Math.ceil(first/tickStep)*tickStep;
    const tickTimes=fiveMinuteGrid?Array.from({length:Math.max(0,Math.floor((last-alignedStart)/tickStep)+1)},(_,i)=>alignedStart+i*tickStep):Array.from({length:Math.min(5,tickCount+1)},(_,i)=>first+i/Math.min(4,tickCount)*duration);
    const minimumTickGap=80/(width-left-right)*duration;
    if(fiveMinuteGrid&&(!tickTimes.length||tickTimes[0]-first>minimumTickGap)) tickTimes.unshift(first);
    if(fiveMinuteGrid&&last-tickTimes.at(-1)>minimumTickGap) tickTimes.push(last);
    for(let i=0;i<tickTimes.length;i++) {
      const at=left+(tickTimes[i]-first)/duration*(width-left-right), time=duration<300000?clockSeconds(tickTimes[i]):clock(tickTimes[i]);
      grid+=`<line x1="${at}" y1="${top}" x2="${at}" y2="${height-bottom}" stroke="#f0f4f1" vector-effect="non-scaling-stroke"/><text x="${at}" y="${height-9}" text-anchor="${i===0?"start":i===tickTimes.length-1?"end":"middle"}" fill="#526980" font-size="12">${time}</text>`;
    }
    if(premium) grid+=`<text x="${left}" y="13" fill="#526980" font-size="11">Premium ₹</text>`;
  }
  const segments=lines.length?lines.map(s=>{
    let previous=null;
    const linePath=s.points.map(r=>{if(!known(r.value)||!Number.isFinite(Date.parse(r.at))){previous=null;return '';}const move=previous&&Date.parse(r.at)-Date.parse(previous.at)<=gapSeconds*1000?'L':'M';previous=r;return `${move}${x(r).toFixed(2)},${y(r.value).toFixed(2)}`;}).join(' ');
    const last=s.points.filter(r=>known(r.value)).at(-1);
    return `<path class="pnl-path" data-series="${escapeHTML(s.key)}" d="${linePath}" fill="none" stroke="${s.color}" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"><title>${escapeHTML(s.name)}</title></path>${last?`<circle cx="${x(last)}" cy="${y(last.value)}" r="3" fill="${s.color}"/>`:''}`;
  }).join(''):pnl?rows.slice(1).map((r,i)=>Date.parse(r.at)-Date.parse(rows[i].at)<=gapSeconds*1000?`<path d="M${x(rows[i])},${y(rows[i].value)} L${x(r)},${y(r.value)}" fill="none" stroke="${r.value>=0?"#08b75d":"#ef4b2c"}" stroke-width="2" vector-effect="non-scaling-stroke"/>`:'').join(""):`<path class="price-path" d="${path}" fill="none" stroke="${color}" stroke-width="${mini?1.6:1.8}" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>`;
  const markerColors={ENTRY:'#34649a',SL:'#bf3e2f',TARGET:'#087f3f'};
  let previousLabel=-Infinity;
  const levelLines=[...markers].sort((a,b)=>b.price-a.price).map(p=>{
    const labelY=Math.min(height-bottom-10,Math.max(top+10,y(p.price)-12,previousLabel+22)); previousLabel=labelY;
    const text=(p.kind==='ENTRY'?'Entry':p.kind)+' '+inr(p.price), tagWidth=text.length*6.6+16;
    return `<line class="price-level" data-level-kind="${p.kind}" x1="${left}" x2="${width-right}" y1="${y(p.price)}" y2="${y(p.price)}" stroke="${markerColors[p.kind]}" stroke-width="1" stroke-dasharray="5 5" vector-effect="non-scaling-stroke"><title>${p.kind==='ENTRY'?'Average entry':p.kind} ${escapeHTML(inr(p.price))}</title></line>${premium?`<g class="level-tag"><rect x="${left+8}" y="${labelY-14}" width="${tagWidth}" height="20" rx="4" fill="#fff" stroke="#e4eae7"/><text x="${left+16}" y="${labelY}" fill="${markerColors[p.kind]}" font-size="11" font-weight="600">${escapeHTML(text)}</text></g>`:''}`;
  }).join('');
  const gaps=premium?rows.slice(1).map((r,i)=>Date.parse(r.at)-Date.parse(rows[i].at)>gapSeconds*1000?`<g class="data-gap"><title>No observations ${clockSeconds(rows[i].at)}–${clockSeconds(r.at)}</title><rect x="${x(rows[i])}" y="${top}" width="${x(r)-x(rows[i])}" height="${height-top-bottom}" fill="#f1f4f3"/>${x(r)-x(rows[i])>65?`<text x="${(x(r)+x(rows[i]))/2}" y="${height-bottom-10}" text-anchor="middle" fill="#526980" font-size="11">No data</text>`:''}</g>`:'').join(''):'';
  const lastRow=rows.at(-1), lastY=y(lastRow.value);
  const lastLabel=premium?`<line x1="${left}" x2="${width-right}" y1="${lastY}" y2="${lastY}" stroke="${color}" stroke-opacity=".35" stroke-dasharray="2 5" vector-effect="non-scaling-stroke"/><rect x="${width-right+8}" y="${lastY-11}" width="64" height="22" rx="4" fill="#172d48"/><text x="${width-right+40}" y="${lastY+4}" text-anchor="middle" fill="white" font-size="12" font-weight="600">${lastRow.value.toFixed(2)}</text>`:'';
  container.innerHTML=`<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${escapeHTML(label)} · ${rows.length} observations"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop stop-color="${color}" stop-opacity=".12"/><stop offset="1" stop-color="${color}" stop-opacity=".015"/></linearGradient></defs>${gaps}${grid}${area&&!hasGaps?`<path d="${fill}" fill="url(#${id})"/>`:""}${pnl?`<line x1="${left}" y1="${base}" x2="${width-right}" y2="${base}" stroke="#bdcec3"/>`:""}${levelLines}${lastLabel}${segments}${mini||lines.length?"":`<circle cx="${x(lastRow)}" cy="${lastY}" r="3" fill="${color}" stroke="white" stroke-width="1.5"/>`}<g class="chart-crosshair" visibility="hidden"><line y1="${top}" y2="${height-bottom}" stroke="#789082" stroke-dasharray="3 4" vector-effect="non-scaling-stroke"/><circle r="4" fill="white" stroke="${color}" stroke-width="1.5"/></g></svg>`;
  if(mini) return;
  const tooltip=document.createElement("div"); tooltip.className="chart-tooltip"; tooltip.hidden=true; container.append(tooltip);
  container.onpointermove=event=> {
    const box=container.getBoundingClientRect(), relative=(event.clientX-box.left)/box.width*width;
    const nearest=rows.reduce((best,r)=>Math.abs(x(r)-relative)<Math.abs(x(best)-relative)?r:best,rows[0]);
    const crosshair=container.querySelector('.chart-crosshair'); if(!crosshair) return; crosshair.setAttribute('visibility','visible');
    const vertical=crosshair.querySelector('line'); vertical.setAttribute('x1',x(nearest));vertical.setAttribute('x2',x(nearest));
    const dot=crosshair.querySelector('circle');dot.setAttribute('cx',x(nearest));dot.setAttribute('cy',y(nearest.value));
    tooltip.innerHTML=`${escapeHTML(fiveMinuteGrid?clockSeconds(nearest.at):clock(nearest.at))} ${timeZone==="Asia/Tokyo"?"JST":"IST"}`+(lines.length?lines.map(s=>`<b><span style="color:${s.color}">${escapeHTML(s.name)}</span> ${escapeHTML(signed(s.points.find(p=>p.at===nearest.at)?.value))}</b>`).join(''):`<b>${escapeHTML(inr(nearest.value))}</b>`);
    tooltip.hidden=false; tooltip.style.left=Math.max(0,Math.min(event.clientX-box.left+12,box.width-tooltip.offsetWidth-4))+"px";
  };
  container.onpointerleave=()=>{tooltip.hidden=true;container.querySelector('.chart-crosshair')?.setAttribute('visibility','hidden');};
}
function renderIndices() {
  $("indices").innerHTML=[...(data.markets||[])].sort((a,b)=>["NIFTY","SENSEX","BANKNIFTY"].indexOf(a.index)-["NIFTY","SENSEX","BANKNIFTY"].indexOf(b.index)).map(m=> {
    const positive=!known(m.change)||m.change>=0, name=m.index==="NIFTY"?"NIFTY 50":m.index==="BANKNIFTY"?"BANK NIFTY":"SENSEX";
    return `<article class="card index-card"><div class="index-top"><div class="index-info"><h2 class="index-name">${name}</h2><strong class="index-price">${known(m.price)?numeric.format(m.price):"—"}</strong><div class="index-change ${known(m.change)?positive?"positive":"negative":""}">${known(m.change)?`${m.change>=0?"+":""}${numeric.format(m.change)} (${percent(m.change_pct)}) ${positive?"▲":"▼"}`:"Change unavailable"}</div></div><div id="mini-${m.index}" class="mini-chart"></div></div><div class="index-details"><span>H: <b>${known(m.high)?numeric.format(m.high):"—"}</b></span><span>L: <b>${known(m.low)?numeric.format(m.low):"—"}</b></span><span>Prev: <b>${known(m.previous)?numeric.format(m.previous):"—"}</b></span></div></article>`;
  }).join("");
  for(const m of data.markets||[]) {
    const container=$("mini-"+m.index);
    const series=chartDetail==='bars'?(m.bar_series||m.series):m.series;
    if(series?.length>=2) chart(container,series,{mini:true,area:true,color:known(m.change)&&m.change<0?"#ef4b2c":"#08b75d",label:m.index+" index",gapSeconds:chartDetail==='live'&&m.series_source==='GROWW_OBSERVED_LTP'?20:Infinity});
    else container.innerHTML="";
  }
}
function renderOptions(reset=false) {
  const activeOptions=market=>(market?.options||[]).filter(o=>o.order_status==='POSITION'&&known(o.quantity)&&o.quantity>0);
  const withOrders=(data.markets||[]).filter(m=>activeOptions(m).length);
  if(withOrders.length&&!withOrders.some(m=>m.index===selectedIndex)) selectedIndex=withOrders[0].index;
  $("chart-toolbar").hidden=!withOrders.length; $("option-grid").hidden=!withOrders.length;
  $("orders-empty").hidden=Boolean(withOrders.length);
  $("orders-empty").textContent=data.positions_status==="AVAILABLE"?"No active option positions. Buy and sell charts appear only while a position is open.":"Positions could not be fully checked. Charts appear only for confirmed active positions.";
  for(const tab of document.querySelectorAll(".tab")) {const active=tab.dataset.index===selectedIndex; tab.classList.toggle("active",active); tab.setAttribute("aria-pressed",String(active));tab.disabled=!withOrders.some(m=>m.index===tab.dataset.index);}
  const market=(data.markets||[]).find(m=>m.index===selectedIndex), options=activeOptions(market);
  let visible=0;
  for(const side of ["buy","sell"]) {
    const legs=options.filter(o=>o.side===side.toUpperCase());
    $(side+"-card").hidden=!legs.length; if(legs.length) visible++;
    if(reset||!legs.some(o=>o.symbol===contracts[side])) contracts[side]=legs[0]?.symbol||null;
    const select=$(side+"-option");
    const markup=legs.map(o=>`<option value="${escapeHTML(o.symbol)}">${escapeHTML(known(o.strike)?`${o.index} ${numeric.format(o.strike)} ${o.type}`:o.symbol)} · ${escapeHTML(o.expiry||"Expiry unverified")}</option>`).join("");
    if(select.innerHTML!==markup) select.innerHTML=markup;
    select.value=contracts[side]||""; select.disabled=!legs.length;
    drawOption(side, legs.find(o=>o.symbol===contracts[side]));
  }
  $("option-grid").classList.toggle("single",visible===1);
}
function drawOption(side, option) {
  let legend=$(side+'-levels');
  if(!legend) {legend=document.createElement('div');legend.id=side+'-levels';legend.className='level-legend';$(side+'-chart').before(legend);}
  if(!option) {legend.innerHTML='';$(side+'-chart').innerHTML='';delete $(side+'-chart').dataset.chartSignature;return;}
  const levels=option?.price_levels||[];
  let tools=$(side+'-chart-tools');
  if(!tools) {
    tools=document.createElement('div');tools.id=side+'-chart-tools';tools.className='option-chart-tools';legend.before(tools);
    tools.innerHTML=`<span>Price history</span><div class="chart-range" role="group" aria-label="${side} chart time range">${[['session','Session'],['30','30 min'],['15','15 min']].map(([value,text])=>`<button type="button" data-range="${value}">${text}</button>`).join('')}</div>`;
    tools.addEventListener('click',event=>{const button=event.target.closest('button[data-range]');if(button){chartRange=button.dataset.range;renderOptions();}});
  }
  for(const button of tools.querySelectorAll('button')) button.setAttribute('aria-pressed',String(button.dataset.range===chartRange));
  legend.innerHTML=levels.map(p=>`<span class="level-${p.kind.toLowerCase()}">${p.kind==='ENTRY'?(side==='buy'?'Bought avg':'Sold avg'):p.kind} <b>${escapeHTML(inr(p.price))}</b>${p.kind!=='ENTRY'&&known(p.quantity)&&p.quantity<option.quantity?` <small>(${p.quantity}/${option.quantity} units)</small>`:''}</span>`).join('');
  if(!levels.some(p=>p.kind==='ENTRY')&&option) legend.insertAdjacentHTML('afterbegin','<span>Entry price unavailable</span>');
  if(option&&option.protective_levels_status!=='AVAILABLE'&&!data.demo) legend.insertAdjacentHTML('beforeend','<span>SL/target check incomplete</span>');
  setAmount(side+"-price",option?.last_price);
  let positionPnl=$(side+'-position-pnl');
  if(!positionPnl){positionPnl=document.createElement('p');positionPnl.id=side+'-position-pnl';positionPnl.className='position-pnl';$(side+'-price').closest('.quote-line').after(positionPnl);}
  const pnlOwner=option.pnl_bucket==='algo'?'Algo':option.pnl_bucket==='self'?'Self trade':'Unassigned';
  positionPnl.innerHTML=`<span>${pnlOwner} · open P&amp;L</span><b class="${known(option.open_pnl_inr)?option.open_pnl_inr<0?'negative':'positive':''}">${escapeHTML(signed(option.open_pnl_inr))}</b><small>Before charges</small>`;
  const allSeries=chartDetail==='bars'?(option?.bar_series||option?.series):option?.series;
  const cutoff=chartRange==='session'?0:Date.parse(allSeries?.at(-1)?.at)-Number(chartRange)*60000;
  const series=allSeries?.filter(p=>Date.parse(p.at)>=cutoff);
  const observed=chartDetail==='live'&&option?.series_source==='GROWW_OBSERVED_LTP';
  const initial=allSeries?.[0]?.value, change=known(initial)&&known(option?.last_price)?option.last_price-initial:null;
  $(side+'-period').textContent=observed?'Live · 5s target · 5-min grid':'5-minute closes';
  $(side+'-chart-updated').textContent=series?.length?`Line through ${clockSeconds(series.at(-1).at)} ${timeZone==='Asia/Tokyo'?'JST':'IST'} · ${series.length} ${data.demo?'sample':'actual'} points${observed?' · reception times':''}`:'Waiting for real price observations';
  $(side+"-change").textContent=known(change)?`${change>=0?"+":""}${change.toFixed(2)}${initial>0?` (${percent(change/initial*100)})`:""}`:"";
  $(side+"-change").title=observed?"Change versus the first observed session price":"Change versus the first completed 5-minute closing price"; tone($(side+"-change"),change);
  $(side+"-book").textContent=option?`Bid ${inr(option.bid)} · Ask ${inr(option.ask)}`:"Bid — · Ask —";
  const owner=option?.ownership==="ENGINE_VERIFIED"?"Engine verified":option?.ownership==="ENGINE_PENDING_VERIFIED"?"Engine pending":"Manual / unknown · protected";
  $(side+"-expiry").textContent=option?`${owner} · Open position · ${option.expiry||"Expiry unverified"}`:"Expiry —";
  if(series?.length) chart($(side+"-chart"),series,{color:change<0?"#ef4b2c":side==="buy"?"#08b75d":"#fb861c",label:side+" option premium · "+(observed?'observed live line':'five-minute closes')+" · "+option.symbol,levels,fiveMinuteGrid:true,gapSeconds:observed?20:Infinity});
  else emptyChart($(side+"-chart"),option?.series_status==="WAITING_FOR_SESSION"?"Waiting for the market session":option?.series_status==="LOADING_OPTION_HISTORY"?"Loading 5-minute prices":option?"Option history unavailable":"Choose an option contract",option?.series_status==="WAITING_FOR_SESSION"?"Today's line appears after real session observations arrive.":option?.series_status==="LOADING_OPTION_HISTORY"?"Quotes continue updating while the line chart loads.":option?"This exact contract has no price history.":"Your selected strike and expiry will appear here.");
}
function renderControl() {
  const c=data.control, policy=c?.strategy_rules, algo=c?.algo;
  const on=algo?.desired_enabled===true;
  $("monitor-status").textContent=data.demo?"Sample preview":on?"On · trading blocked":"Algo Off";
  $("engine-heading").textContent=data.runtime?.host==='ORACLE'?"Oracle engine":"Trading engine";
  $("vm-status").textContent=data.vm?`VM: ${data.vm.status} · independent PC monitor${data.vm.alert_status?` · alert ${data.vm.alert_status}`:''}`:"";
  $("email-status").textContent=data.daily_email?`Daily visual email · 19:30 JST · ${data.daily_email.status} · inbox ${data.daily_email.inbox_verified?'verified':'not verified'}`:"Daily visual email · 19:30 JST";
  $("everyday-rule").textContent="NIFTY call ₹20 · SENSEX call ₹80";
  $("slot-policy").textContent="1 algo basket · maximum 2 lots · margin required";
  $("monitor-schedule").textContent="14:00–19:00 JST · expiry strategy at 18:00";
  const expiry=c?.expiry_check;
  $("expiry-status").textContent=expiry?Object.entries(expiry).map(([index,e])=>`${index}: ${e.is_expiry_day===true?"expiry — Everyday skips; Late-session eligible":e.is_expiry_day===false?"non-expiry — Everyday eligible":"expiry unverified — no entry"}`).join(" · "):"Actual expiry dates must be confirmed from Groww";
  const prep=data.execution_preparation;
  $("preparation-status").hidden=!prep;
  const bookLabels={TARGET_SHORT_MANUAL_PROTECTED:"target belongs to manual-trade history",TARGET_SHORT_MASTER_REQUIRED:"waiting for target contract metadata",TARGET_SHORT_BOOK_REQUIRED:"waiting for the target's bid/ask depth",SHORT_BOOK_EXPIRED_DURING_CALCULATIONS:"price expired during broker calculations; waiting for a fresh comparison",FRESH_EXCLUSIVE_HEDGE_BOOK_REQUIRED:"waiting for a current unprotected hedge book"};
  const prepLabels={RANKED_CURRENT_CANDIDATES:"sampled margin comparison prepared",NO_VERIFIED_AFFORDABLE_HEDGED_CANDIDATE:"no verified affordable spread",CURRENT_EXPIRY_REQUIRED:"waiting for expiry evidence",ACTUAL_EXPIRY_DAY_SKIPPED:"Everyday skips expiry",WAIT_FOR_ACTUAL_EXPIRY_1800_JST:"waiting for expiry at 18:00",VERIFIED_DIRECTIONAL_TREND_REQUIRED:"waiting for an observed expiry trend",TARGET_SHORT_BOOK_OR_EXCLUSIVE_CONTRACT_REQUIRED:"target contract unavailable or protected",FRESH_COMPLETE_DATA_REQUIRED:"waiting for current data",OUTSIDE_1400_1900_JST:"outside entry window",BROKER_PREPARATION_UNAVAILABLE:"broker calculation unavailable",PREPARATION_NOT_STARTED:"waiting for broker preparation",PREPARATION_INPUT_UNAVAILABLE:"preparation data unavailable",ENGINE_SLOT_OCCUPIED_RECONCILE_BEFORE_ENTRY:"owned basket occupies the slot"};
  $("preparation-status").textContent=prep?`Preparation: ${bookLabels[prep.reason]||prepLabels[prep.reason]||prep.status}${prep.at?' · '+dateTime(prep.at):''} · broker writes disabled`:"";
  $("strategy-priority").textContent="NIFTY Mon/Tue/Fri; SENSEX Wed/Thu. Expiry: a matching position skips entry; otherwise replace only the algo-owned basket. Your trades are protected.";
  $("levels").hidden=true; $("rule-details").hidden=true;
  $("analysis-status").textContent="Short premium below ₹8: review closing the short and replacing it with the next listed premium above ₹8. Keep the bought hedge unless the basket's improvement after costs exceeds ₹100.";
  $("trailing-status").textContent="Loss-stop limit ₹2,000 per algo basket · protection is not deployed; this is a trigger, not a guaranteed loss cap.";
  $("control-note").textContent="By 19:00: hold when the short premium is above its entry premium − ₹5; otherwise review a return to ₹20 / ₹80. Expiry at 18:00: uptrend → put 3 strikes below ATM; downtrend → call 3 strikes above ATM. Hedges are required. Oracle execution is unfinished.";
  $("algo-start").disabled=data.demo||algoStartPending||on;
  $("algo-start").textContent=on?"Algo On":"Algo Start";
  $("algo-stop").disabled=data.demo||algoStartPending||!on;
  const last=algoStartResult||c?.latest_start_request;
  const labels={ORACLE_EXECUTOR_NOT_IMPLEMENTED_OR_VERIFIED:"Oracle order executor is unfinished",REPOSITORY_PAUSED:"trading is paused",FRESH_MARKET_DATA_REQUIRED:"current data is required",OUTSIDE_1400_1900_JST:"outside 14:00–19:00 JST",OFFLINE_VIEW:"offline view",MAXIMUM_LOTS_REQUIRED:"lot cap missing",PREMIUM_POLICY_REQUIRED:"strategy settings are missing",OWNER_ALGO_OFF:"owner setting is Off",VM_UNHEALTHY_OR_STALE:"VM heartbeat/data unavailable"};
  $("algo-start-result").textContent=algoStartPending?"Saving owner setting…":on?`On is saved until you click Algo Off. Trading blocked: ${(algo.blockers||[]).map(k=>labels[k]||k).join('; ')}.`:last?.status==='TRANSPORT_FAILED'?"Could not save the setting on Oracle. No change confirmed.":"Algo Off. Start saves your On preference across restarts; it does not bypass blocked trading readiness.";
}
let withdrawalPending=false;
const pendingWithdrawalKey='options-trader-pending-withdrawal-v1';
function jstDay() {return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}
function renderCapital() {
  const ledger=data.capital_summary;
  setAmount('capital-invested',ledger?.invested_inr);
  setAmount('capital-withdrawn',ledger?.withdrawn_inr);
  setAmount('capital-api-fees',ledger?.api_fees_inr);
  setAmount('capital-remaining',ledger?.remaining_capital_inr);
  const month=ledger?.fees_through_month;
  const through=month?new Intl.DateTimeFormat('en',{month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(month+'-01T00:00:00Z')):'—';
  const state=ledger?.status;
  $('capital-ledger-status').textContent=data.demo?'Sample capital · design preview':state==='AVAILABLE'?`Owner ledger · API ${inr(ledger.monthly_api_fee_inr)}/month · through ${through}`:state==='CACHED'?`Saved owner ledger · ${dateTime(ledger.updated_at)}`:state==='FEE_UPDATE_PENDING'?'Monthly fee update pending · waiting for Oracle':state==='INVALID_LEDGER'?'Capital ledger unavailable':'Capital ledger not configured';
  $('record-withdrawal').disabled=demo||withdrawalPending||state!=='AVAILABLE'||Boolean(data.vm&&data.vm.status!=='HEALTHY');
}
function renderAccount() {
  const a=data.account;
  const p=data.broker_pnl, actual=!data.demo, split=Boolean(p);
  setAmount("today-pnl",actual?p?.total_inr:a.today_pnl_inr,true); $("today-return").hidden=actual;
  $("today-return").textContent=percent(a.today_return_pct); tone($("today-return"),a.today_return_pct);
  setAmount("realized",actual?p?.realized_inr:a.realized_today_inr,true); setAmount("unrealized",actual?p?.unrealized_inr:a.unrealized_inr,true); $("trade-count").textContent=(actual?p?.closed_contracts:a.trade_count_today)??"—";
  const funds=data.funds;
  setAmount("capital",data.demo?a.capital_inr:funds?.clear_cash_inr);
  setAmount("used-margin",data.demo?a.used_margin_inr:funds?.total_margin_used_inr);
  setAmount("available-margin",data.demo?a.available_margin_inr:funds?.option_sell_available_inr);
  setAmount("option-buy-money",data.demo?a.available_margin_inr:funds?.option_buy_available_inr);
  setAmount("collateral-money",data.demo?0:funds?.collateral_available_inr);
  $("funds-status").textContent=data.demo?"Sample cash · design preview":funds?.status==='AVAILABLE'?`Clear cash · Groww ${dateTime(funds.received_at)} · refresh 30s`:funds?.status==='STALE'?"Groww money is stale · waiting for a new read":"Groww money unavailable · amounts stay unknown";
  const pnlStatus=p?.status==='AVAILABLE'?`Index options · before charges · ${dateTime(p.received_at)}`:p?.status==='CARRY_DAY_BASIS_UNVERIFIED'?"Overnight position · today's P&L basis unverified":p?.status==='STALE'?"P&L is stale · waiting for Groww":"P&L incomplete · waiting for current positions and prices";
  $("account-status").textContent=data.demo?"Sample results · design preview":pnlStatus;
  setAmount("chart-pnl",actual?p?.total_inr:a.today_pnl_inr,true); setAmount("portfolio-value",a.portfolio_value_inr);
  const groups=[{key:'self',name:'Self trades',color:'#2473b5'},{key:'algo',name:'Algo',color:'#8853ca'}];
  if(p?.buckets?.unassigned?.contracts>0||(p?.series||[]).some(r=>known(r.unassigned)&&r.unassigned!==0)) groups.push({key:'unassigned',name:'Unassigned / mixed',color:'#b46c15'});
  $("pnl-breakdown").hidden=!split;
  $("pnl-breakdown").innerHTML=groups.map(g=>`<div class="pnl-bucket"><span><i style="background:${g.color}"></i>${g.name}</span><strong class="${known(p?.buckets?.[g.key]?.total_inr)?p.buckets[g.key].total_inr<0?'negative':'positive':''}">${escapeHTML(signed(p?.buckets?.[g.key]?.total_inr))}</strong><small>Realised ${escapeHTML(signed(p?.buckets?.[g.key]?.realized_inr))} · Open ${escapeHTML(signed(p?.buckets?.[g.key]?.unrealized_inr))}</small></div>`).join('');
  $('summary-pnl-split').innerHTML=groups.map(g=>`<span><i style="background:${g.color}"></i>${g.name} <b class="${known(p?.buckets?.[g.key]?.total_inr)?p.buckets[g.key].total_inr<0?'negative':'positive':''}">${escapeHTML(signed(p?.buckets?.[g.key]?.total_inr))}</b></span>`).join('');
  if(split&&(p?.series||[]).some(r=>known(r.self)||known(r.algo)||known(r.unassigned))) {
    const lines=groups.map(g=>({...g,points:p.series.map(r=>({at:r.at,value:r[g.key]}))}));
    const points=p.series.map(r=>({at:r.at,value:known(r.self)?r.self:known(r.algo)?r.algo:r.unassigned}));
    chart($("pnl-chart"),points,{pnl:true,label:"Today's index-option P&L · self trades and verified algo · before charges",lines,fiveMinuteGrid:true,gapSeconds:data.demo?Infinity:20});
  } else if(!actual&&a.pnl_series?.length>=2) chart($("pnl-chart"),a.pnl_series,{area:true,pnl:true,label:"Sample net profit and loss"});
  else emptyChart($("pnl-chart"),"Waiting for P&L observations", "Real observations appear as Groww positions and prices arrive.");
  const firstPnl=p?.series?.find(r=>known(r.self)||known(r.algo)||known(r.unassigned));
  $("pnl-caption").textContent=data.demo?"Sample results · design preview":`${pnlStatus}${firstPnl?` · Chart recorded from ${clockSeconds(firstPnl.at)}`:''}. Self includes non-journal trades; only exact journal matches count as algo. Mixed trades stay unassigned.`;
  if(a.portfolio_series?.length>=2) chart($("portfolio-chart"),a.portfolio_series,{color:"#fb861c",area:true,label:"Reviewed portfolio value"});
  else emptyChart($("portfolio-chart"),"No portfolio history yet","Your account values are never estimated from index moves.");
  $("strategy-count").textContent=data.strategies.length;
  $("strategy-rows").innerHTML=data.strategies.map((s,i)=>`<tr><td><div class="strategy-info ${known(s.net_pnl_inr)&&s.net_pnl_inr<0?"negative":""}"><span class="strategy-number">${i+1}</span><div class="strategy-content"><div class="strategy-name">${escapeHTML(s.name)}</div><p class="strategy-description">${escapeHTML(s.description)}</p><div class="outcome-track" role="img" aria-label="${escapeHTML(s.name)}: ${s.closed_trades?`${s.non_loss_pct.toFixed(1)}% non-losing, ${s.loss_pct.toFixed(1)}% losing, ${s.closed_trades} closed trades`:"No results"}"><span class="profit-segment" style="width:${s.non_loss_pct??0}%"></span><span class="loss-segment" style="width:${s.loss_pct??0}%"></span></div><span class="outcome-caption">${s.closed_trades?`${s.non_loss_pct.toFixed(0)}% non-loss · ${s.loss_pct.toFixed(0)}% loss · ${s.closed_trades} trades`:"No results"}</span></div></div></td><td class="${known(s.return_pct)?s.return_pct<0?"negative":"positive":""}">${percent(s.return_pct)}</td><td class="${known(s.net_pnl_inr)?s.net_pnl_inr<0?"negative":"positive":""}">${signed(s.net_pnl_inr)}</td></tr>`).join("");
}
function render() {
  if(!data) return;
  renderCapital(); renderIndices(); renderOptions(); renderAccount(); renderControl();
  const recent=data.freshness==="RECENT";
  $("data-status").textContent=data.demo?"Sample data":data.freshness==="NO_DATA"?"No market data":recent?"Latest snapshot":"Saved snapshot";
  $("data-status").className="status-pill"+(data.demo?" demo":!recent?" stale":"");
  $("as-of").textContent=data.as_of?`${dateTime(data.as_of)} ${timeZone==="Asia/Tokyo"?"JST":"IST"}`:"Refresh to read Groww data";
  $("footer-source").textContent=data.demo?"Design preview · all figures simulated":`Groww observations · ${timeZone==="Asia/Tokyo"?"JST":"IST"} · Viewing only`;
  const message=data.demo?"DESIGN PREVIEW — sample prices and results. Switch off sample data in settings to return to your actual snapshots.":data.refresh_error||data.market_status==="BLOCKED"?"The latest broker read failed. Check Groww API approval, then refresh again.":data.offline?"Offline viewing. Showing saved observations; broker refresh is disabled.":data.market_status==="PARTIAL_MARKET_DATA"?"Some Groww quote reads failed. Missing values remain unavailable.":data.orders_status==="INCOMPLETE"?"Some orders or positions could not be fully checked. Only confirmed contracts are shown.":data.freshness==="STALE"?"Showing a saved snapshot. Refresh data to request a current read.":null;
  $("notice").hidden=!message; $("notice").textContent=message||"";
  $("refresh").disabled=refreshPending||demo||data.offline;
  $("refresh").classList.toggle("loading",refreshPending);
  $("refresh").querySelector("span").textContent=refreshPending?"Reading Groww…":autoRefresh&&data.refreshing?"Refreshing · 5s":"Refresh data";
}
async function load() {
  try {
    const response=await fetch("/api/dashboard",{cache:"no-store"});
    if(!response.ok) throw new Error("local-data-unavailable");
    const value=await response.json(), changed=!liveData||JSON.stringify(value)!==JSON.stringify(liveData);
    liveData=value;
    if(!demo&&changed) {data=liveData;render();}
  } catch {$("notice").hidden=false;$("notice").textContent="The local engine is unavailable. Reopen Options Trader from Desktop or Start.";$("data-status").textContent="Disconnected";}
}
async function refresh() {
  if(refreshPending||demo||liveData?.offline) return;
  refreshPending=true;lastRequest=Date.now();render();
  try {
    const response=await fetch("/api/refresh",{method:"POST",headers:{"X-Local-Token":document.querySelector('meta[name="local-token"]').content}});
    if(!response.ok) throw new Error("refresh-unavailable");
  } catch {$("notice").hidden=false;$("notice").textContent="Could not start the local market reader. Try refreshing again.";}
  finally {refreshPending=false;await load();render();}
}
function sampleData() {
  // Explicit design preview only. Never returned by the actual data API or journal.
  const start=Date.parse("2026-10-01T09:15:00+05:30");
  function series(initial,delta,n=72) {return Array.from({length:n},(_,i)=>{const value=initial+delta*i/(n-1)+Math.sin(i*.48)*Math.abs(delta)*.045+Math.sin(i*1.7)*Math.abs(delta)*.012, open=value+Math.sin(i*.77)*Math.abs(delta)*.03;return {at:new Date(start+i*300000).toISOString(),value,open,high:Math.max(open,value)+Math.abs(delta)*.02,low:Math.min(open,value)-Math.abs(delta)*.02};});}
  const specs=[["NIFTY",22368,124.5],["BANKNIFTY",48332.6,-120.45],["SENSEX",74967.18,456.1]];
  const markets=specs.map(([index,price,change])=>({index,price,change,change_pct:change/(price-change)*100,high:price+74,low:price-210,previous:price-change,series:series(price-change,change),options:[0,1].map((n)=>({symbol:`SAMPLE-${index}-${n}-CE`,index,type:"CE",side:n?"BUY":"SELL",order_status:"POSITION",quantity:65,pnl_bucket:n?"self":"algo",open_pnl_inr:n?-1170:1430,protective_levels_status:"AVAILABLE",price_levels:[{kind:"ENTRY",price:[220,100][n],quantity:65},{kind:"SL",price:[260,65][n],quantity:65},{kind:"TARGET",price:[130,150][n],quantity:65}],exchange:index==="SENSEX"?"BSE":"NSE",expiry:index==="SENSEX"?"2026-10-08":"2026-10-06",strike:Math.ceil(price/100)*100+n*(index==="SENSEX"?800:400),last_price:[198,82][n],bid:[197,81][n],ask:[199,83][n],series:series([245,120][n],[-47,-38][n]),series_status:"SAMPLE"}))}));
  const rows=[{id:"everyday",name:"Everyday hedged call",description:"13:15 JST · NIFTY +500 / SENSEX +1,000",closed_trades:8,non_loss_pct:100,loss_pct:0,net_pnl_inr:8450,return_pct:28.2},{id:"late_session",name:"Late-session decay",description:"17:45–18:45 JST · Hedged calls",closed_trades:10,non_loss_pct:60,loss_pct:40,net_pnl_inr:-3200,return_pct:-12.8},{id:"swing",name:"Swing call spread",description:"Overnight · 10 / 20 strike study",closed_trades:12,non_loss_pct:75,loss_pct:25,net_pnl_inr:12600,return_pct:18},{id:"expiry_reversal",name:"Expiry reversal",description:"SENSEX · 18:50 JST onward",closed_trades:5,non_loss_pct:80,loss_pct:20,net_pnl_inr:6100,return_pct:24.4}];
  const pnl=series(0,24850,40).map((r,i)=>({...r,value:r.value-(i>2&&i<12?Math.sin((i-2)/10*Math.PI)*10000:0)}));
  pnl.at(-1).value=24850;
  const samplePnl={status:"SAMPLE",buckets:{self:{realized_inr:14272,unrealized_inr:1632,total_inr:15904,contracts:4},algo:{realized_inr:8028,unrealized_inr:918,total_inr:8946,contracts:4},unassigned:{contracts:0,total_inr:0}},series:pnl.map(r=>({at:r.at,self:r.value*.64,algo:r.value*.36,unassigned:0}))};
  return {demo:true,broker_pnl:samplePnl,orders_status:"AVAILABLE",positions_status:"AVAILABLE",as_of:new Date(start+71*300000).toISOString(),freshness:"SAMPLE",markets,strategies:rows.filter(r=>["everyday","late_session"].includes(r.id)).map(r=>({...r,description:r.id==="everyday"?"14:00–19:00 JST · NIFTY ₹20 / SENSEX ₹80 call":"Actual expiry · 18:00 JST · 3 strike intervals from ATM"})),account:{status:"SAMPLE",capital_inr:150000,portfolio_value_inr:218450,used_margin_inr:112000,available_margin_inr:38000,margin_utilization_pct:74.67,today_pnl_inr:24850,today_return_pct:16.57,realized_today_inr:22300,unrealized_inr:2550,trade_count_today:8,pnl_series:pnl,portfolio_series:series(190000,28450,25)}};
}
async function setAlgo(enabled){
  if(algoStartPending||demo) return;
  algoStartPending=true;renderControl();
  try {
    const response=await fetch(enabled?'/api/algo/start':'/api/algo/stop',{method:'POST',headers:{'X-Local-Token':document.querySelector('meta[name="local-token"]').content},cache:'no-store'});
    algoStartResult=await response.json();
    await refresh();
  } catch {algoStartResult={status:'TRANSPORT_FAILED',blockers:['LOCAL_ENGINE_UNAVAILABLE']};}
  finally {algoStartPending=false;renderControl();}
}
$("algo-start").addEventListener("click",()=>setAlgo(true));
$("algo-stop").addEventListener("click",()=>setAlgo(false));
function pendingWithdrawal() {try{return JSON.parse(localStorage.getItem(pendingWithdrawalKey));}catch{return null;}}
$('record-withdrawal').addEventListener('click',()=>{
  const pending=pendingWithdrawal();
  $('withdrawal-date').max=jstDay();
  $('withdrawal-date').value=pending?.effective_date||jstDay();
  $('withdrawal-amount').value=pending?.amount_inr||'';
  $('withdrawal-amount').readOnly=Boolean(pending);$('withdrawal-date').readOnly=Boolean(pending);
  $('withdrawal-save').textContent=pending?'Retry record':'Save record';
  $('withdrawal-result').textContent=pending?'Retry the pending record with the same amount and date.':'';
  $('withdrawal-dialog').showModal();
  $('withdrawal-amount').focus();
});
$('withdrawal-close').addEventListener('click',()=>{if(!withdrawalPending)$('withdrawal-dialog').close();});
$('withdrawal-dialog').addEventListener('cancel',event=>{if(withdrawalPending)event.preventDefault();});
$('withdrawal-form').addEventListener('submit',async event=>{
  event.preventDefault();if(withdrawalPending||demo)return;
  const amount=$('withdrawal-amount').value, day=$('withdrawal-date').value;
  const pending=pendingWithdrawal();
  const body=pending||{id:crypto.randomUUID(),amount_inr:amount,effective_date:day};
  try {localStorage.setItem(pendingWithdrawalKey,JSON.stringify(body));}
  catch {$('withdrawal-result').textContent='Browser storage unavailable. Reopen the app before recording.';return;}
  withdrawalPending=true;$('withdrawal-save').disabled=true;$('withdrawal-close').disabled=true;
  $('withdrawal-amount').readOnly=true;$('withdrawal-date').readOnly=true;
  $('withdrawal-result').textContent='Saving…';renderCapital();
  try {
    const response=await fetch('/api/capital/withdrawals',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':document.querySelector('meta[name="local-token"]').content},body:JSON.stringify(body),cache:'no-store'});
    const result=await response.json();
    if(!response.ok||!['RECORDED','ALREADY_RECORDED'].includes(result.status)) {
      const messages={INVALID_AMOUNT:'Enter an INR amount with at most two decimals.',POSITIVE_AMOUNT_REQUIRED:'Enter an amount above zero.',FUTURE_WITHDRAWAL_DATE:'Choose today or an earlier JST date.',INVALID_WITHDRAWAL_DATE:'Choose a valid date.',WITHDRAWAL_ID_CONFLICT:'This record ID already has different details. Keep the pending record for review.',CAPITAL_NOT_CONFIGURED:'The capital ledger is not configured.'};
      $('withdrawal-result').textContent=messages[result.reason]||'Oracle did not confirm the record. Retry with the same amount and date.';
      // A definitive validation rejection has no ledger effect. An uncertain
      // connection/5xx/conflict keeps its durable ID and locked original values.
      if(response.status===400){localStorage.removeItem(pendingWithdrawalKey);$('withdrawal-amount').readOnly=false;$('withdrawal-date').readOnly=false;}
      return;
    }
    localStorage.removeItem(pendingWithdrawalKey);
    $('withdrawal-amount').readOnly=false;$('withdrawal-date').readOnly=false;
    $('withdrawal-result').textContent=result.status==='ALREADY_RECORDED'?'Already recorded · no duplicate':'Withdrawal recorded';
    $('withdrawal-amount').value='';await load();
  } catch {$('withdrawal-result').textContent='Connection interrupted. Retry with the same amount and date.';}
  finally {withdrawalPending=false;$('withdrawal-save').disabled=false;$('withdrawal-close').disabled=false;$('withdrawal-save').textContent=pendingWithdrawal()?'Retry record':'Save record';renderCapital();}
});
$("refresh").addEventListener("click",refresh);
$("settings").addEventListener("click",()=>$("settings-dialog").showModal());
$("settings-dialog").addEventListener("click",event=>{if(event.target===$("settings-dialog")) {const r=$("settings-dialog").getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom) $("settings-dialog").close();}});
$("timezone").value=timeZone;
$("timezone").addEventListener("change",event=>{timeZone=event.target.value;try{localStorage.setItem("trading-timezone",timeZone);}catch{}render();});
$("auto-refresh").addEventListener("change",event=>{autoRefresh=event.target.checked;if(autoRefresh) refresh();});
$("chart-detail").addEventListener("change",event=>{chartDetail=event.target.value;render();});
let resizeFrame;
const chartResize=new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{if(data) render();});});
for(const id of ['buy-chart','sell-chart','pnl-chart','portfolio-chart']) if($(id)) chartResize.observe($(id));
$("demo-toggle").addEventListener("change",event=>{demo=event.target.checked;contracts={buy:null,sell:null};data=demo?sampleData():liveData;render();if(!demo&&autoRefresh) refresh();});
for(const tab of document.querySelectorAll(".tab")) tab.addEventListener("click",()=>{selectedIndex=tab.dataset.index;try{localStorage.setItem("trading-index",selectedIndex);}catch{}contracts={buy:null,sell:null};renderOptions(true);});
for(const side of ["buy","sell"]) $(side+"-option").addEventListener("change",event=>{contracts[side]=event.target.value||null;const market=data.markets.find(m=>m.index===selectedIndex);drawOption(side,market?.options.find(o=>o.symbol===contracts[side]));});
load().then(()=>refresh());
setInterval(async()=>{if(!document.hidden){await load();if(autoRefresh&&!demo&&Date.now()-lastRequest>=4500) await refresh();}},5000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&autoRefresh&&!demo) refresh();});
