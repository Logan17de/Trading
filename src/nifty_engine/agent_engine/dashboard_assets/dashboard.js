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
  container.innerHTML = `<div class="empty-chart"><svg class="icon" aria-hidden="true"><use href="#icon-bars"/></svg><strong>${escapeHTML(title)}</strong><small>${escapeHTML(subtitle)}</small></div>`;
}
function chart(container, points, {color="#08b75d", area=false, mini=false, pnl=false, label="Chart",levels=[],fiveMinuteGrid=false,gapSeconds=Infinity}={}) {
  const rows = (points || []).filter(p => known(p.value) && Number.isFinite(Date.parse(p.at)));
  if (!rows.length || (mini && rows.length < 2)) {emptyChart(container, rows.length ? "Waiting for more observations" : "No chart data", "No prices are filled in or simulated."); return;}
  const markers=levels.filter(p=>known(p.price)&&p.price>0&&['ENTRY','SL','TARGET'].includes(p.kind));
  const signature=JSON.stringify({rows,color,area,mini,pnl,label,markers,timeZone,fiveMinuteGrid,gapSeconds});
  if(container.dataset.chartSignature===signature) return;
  container.dataset.chartSignature=signature;
  const width=640, height=mini?120:250, left=mini?1:47, right=mini?4:15, top=mini?10:14, bottom=mini?7:29;
  const domain=[...rows.map(r=>r.value),...markers.map(p=>p.price)];
  let low=Math.min(...domain), high=Math.max(...domain);
  if (pnl) {low=Math.min(low,0); high=Math.max(high,0);}
  let span=high-low || Math.max(Math.abs(high)*.02,1); low-=span*.09; high+=span*.09; if(!pnl) low=Math.max(0,low); span=high-low;
  const first=fiveMinuteGrid?Math.floor(Date.parse(rows[0].at)/300000)*300000:Date.parse(rows[0].at), last=Date.parse(rows.at(-1).at), duration=last-first||1;
  const inset=0;
  const x=r=>rows.length===1?(width+left-right)/2:left+inset+(Date.parse(r.at)-first)/duration*(width-left-right-2*inset), y=v=>top+(high-v)/span*(height-top-bottom);
  const path=rows.map((r,i)=>`${i&&Date.parse(r.at)-Date.parse(rows[i-1].at)<=gapSeconds*1000?"L":"M"}${x(r).toFixed(2)},${y(r.value).toFixed(2)}`).join(" ");
  const hasGaps=rows.some((r,i)=>i&&Date.parse(r.at)-Date.parse(rows[i-1].at)>gapSeconds*1000);
  const base=pnl?y(0):height-bottom, id="fill-"+container.id;
  const fill=`${path} L${x(rows.at(-1)).toFixed(2)},${base.toFixed(2)} L${x(rows[0]).toFixed(2)},${base.toFixed(2)} Z`;
  let grid="";
  if (!mini) {
    for(let i=0;i<4;i++) {
      const value=low+span*(3-i)/3, at=y(value);
      const tick=Math.abs(value)>=1000?(value/1000).toFixed(Math.abs(value)>=10000?0:1)+"k":value.toFixed(span<10?2:span<100?1:0);
      grid+=`<line x1="${left}" y1="${at}" x2="${width-right}" y2="${at}" stroke="#eaf0ed"/><text x="${left-9}" y="${at+4}" text-anchor="end" fill="#72849a" font-size="10">${tick}</text>`;
    }
    const tickTimes=fiveMinuteGrid?Array.from({length:Math.floor(duration/(Math.max(1,Math.ceil(duration/300000/5))*300000))+1},(_,i)=>first+i*Math.max(1,Math.ceil(duration/300000/5))*300000):Array.from({length:5},(_,i)=>first+i/4*duration);
    for(let i=0;i<tickTimes.length;i++) {
      const at=left+(tickTimes[i]-first)/duration*(width-left-right), time=clock(tickTimes[i]);
      grid+=`<line x1="${at}" y1="${top}" x2="${at}" y2="${height-bottom}" stroke="#f0f4f1"/><text x="${at}" y="${height-8}" text-anchor="${i===0?"start":i===tickTimes.length-1?"end":"middle"}" fill="#72849a" font-size="10">${time}</text>`;
    }
  }
  const segments=pnl?rows.slice(1).map((r,i)=>`<path d="M${x(rows[i])},${y(rows[i].value)} L${x(r)},${y(r.value)}" fill="none" stroke="${r.value>=0?"#08b75d":"#ef4b2c"}" stroke-width="2.5"/>`).join(""):`<path d="${path}" fill="none" stroke="${color}" stroke-width="${mini?2.7:2.6}" stroke-linejoin="round" stroke-linecap="round"/>`;
  const markerColors={ENTRY:'#3974c8',SL:'#dc503f',TARGET:'#0e9c68'};
  const levelLines=markers.map(p=>`<line class="price-level" data-level-kind="${p.kind}" x1="${left}" x2="${width-right}" y1="${y(p.price)}" y2="${y(p.price)}" stroke="${markerColors[p.kind]}" stroke-width="1.5" stroke-dasharray="6 4"><title>${p.kind==='ENTRY'?'Average entry':p.kind} ${escapeHTML(inr(p.price))}</title></line>`).join('');
  container.innerHTML=`<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeHTML(label)} · ${rows.length} observations"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop stop-color="${color}" stop-opacity=".2"/><stop offset="1" stop-color="${color}" stop-opacity=".015"/></linearGradient></defs>${grid}${area&&!hasGaps?`<path d="${fill}" fill="url(#${id})"/>`:""}${pnl?`<line x1="${left}" y1="${base}" x2="${width-right}" y2="${base}" stroke="#bdcec3"/>`:""}${segments}${mini?"":`<circle cx="${x(rows.at(-1))}" cy="${y(rows.at(-1).value)}" r="4.5" fill="${color}" stroke="white" stroke-width="2"/>`}</svg>`;
  container.querySelector('svg').insertAdjacentHTML('beforeend',levelLines);
  if(mini) return;
  const tooltip=document.createElement("div"); tooltip.className="chart-tooltip"; tooltip.hidden=true; container.append(tooltip);
  container.onpointermove=event=> {
    const box=container.getBoundingClientRect(), relative=(event.clientX-box.left)/box.width*width;
    const nearest=rows.reduce((best,r)=>Math.abs(x(r)-relative)<Math.abs(x(best)-relative)?r:best,rows[0]);
    tooltip.innerHTML=`${escapeHTML(fiveMinuteGrid?clockSeconds(nearest.at):clock(nearest.at))} ${timeZone==="Asia/Tokyo"?"JST":"IST"}<b>${escapeHTML(inr(nearest.value))}</b>`;
    tooltip.hidden=false; tooltip.style.left=Math.max(0,Math.min(event.clientX-box.left+12,box.width-tooltip.offsetWidth-4))+"px";
  };
  container.onpointerleave=()=>{tooltip.hidden=true;};
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
  legend.innerHTML=levels.map(p=>`<span class="level-${p.kind.toLowerCase()}">${p.kind==='ENTRY'?(side==='buy'?'Bought avg':'Sold avg'):p.kind} <b>${escapeHTML(inr(p.price))}</b>${p.kind!=='ENTRY'&&known(p.quantity)&&p.quantity<option.quantity?` <small>(${p.quantity}/${option.quantity} units)</small>`:''}</span>`).join('');
  if(!levels.some(p=>p.kind==='ENTRY')&&option) legend.insertAdjacentHTML('afterbegin','<span>Entry price unavailable</span>');
  if(option&&option.protective_levels_status!=='AVAILABLE'&&!data.demo) legend.insertAdjacentHTML('beforeend','<span>SL/target check incomplete</span>');
  setAmount(side+"-price",option?.last_price);
  const series=chartDetail==='bars'?(option?.bar_series||option?.series):option?.series;
  const observed=chartDetail==='live'&&option?.series_source==='GROWW_OBSERVED_LTP';
  const initial=series?.[0]?.value, change=known(initial)&&known(option?.last_price)?option.last_price-initial:null;
  $(side+'-period').textContent=observed?'Live · 5s target · 5-min grid':'5-minute closes';
  $(side+'-chart-updated').textContent=series?.length?`Line through ${clockSeconds(series.at(-1).at)} ${timeZone==='Asia/Tokyo'?'JST':'IST'} · ${series.length} actual points${observed?' · reception times':''}`:'Waiting for real price observations';
  $(side+"-change").textContent=known(change)?`${change>=0?"+":""}${change.toFixed(2)}${initial>0?` (${percent(change/initial*100)})`:""}`:"";
  $(side+"-change").title=observed?"Change versus the first observed session price":"Change versus the first completed 5-minute closing price"; tone($(side+"-change"),change);
  $(side+"-book").textContent=option?`Bid ${inr(option.bid)} · Ask ${inr(option.ask)}`:"Bid — · Ask —";
  const owner=option?.ownership==="ENGINE_VERIFIED"?"Engine verified":option?.ownership==="ENGINE_PENDING_VERIFIED"?"Engine pending":"Manual / unknown · protected";
  $(side+"-expiry").textContent=option?`${owner} · Open position · ${option.expiry||"Expiry unverified"}`:"Expiry —";
  if(series?.length) chart($(side+"-chart"),series,{color:change<0?"#ef4b2c":side==="buy"?"#08b75d":"#fb861c",label:side+" option premium · "+(observed?'observed live line':'five-minute closes')+" · "+option.symbol,levels,fiveMinuteGrid:true,gapSeconds:observed?20:Infinity});
  else emptyChart($(side+"-chart"),option?.series_status==="WAITING_FOR_SESSION"?"Waiting for the market session":option?.series_status==="LOADING_OPTION_HISTORY"?"Loading 5-minute prices":option?"Option history unavailable":"Choose an option contract",option?.series_status==="WAITING_FOR_SESSION"?"Today's line appears after real session observations arrive.":option?.series_status==="LOADING_OPTION_HISTORY"?"Quotes continue updating while the line chart loads.":option?"This exact contract has no price history.":"Your selected strike and expiry will appear here.");
}
function renderControl() {
  const c=data.control;
  $("monitor-status").textContent=data.demo?"Sample preview":data.background_monitor?"Background monitor on":"Use the Options Trader app shortcut";
  $("everyday-rule").textContent=c?.everyday?.minimum_short_call_strike?`${c.everyday.index} · sell call ≥ ${numeric.format(c.everyday.minimum_short_call_strike)}`:"NIFTY +500 · SENSEX +1,000 · from 13:15 JST";
  $("slot-policy").textContent="1 active slot · 1 lot · higher-call hedge";
  $("monitor-schedule").textContent="Research 12:55 / 15:00 / 17:00 · rules 13:00–18:15 JST";
  const news=c?.news;
  $("news-status").textContent=data.demo?"Sample news status":news?`News ${news.risk} · ${news.article_count} dated articles · ${news.feeds.map(f=>`${f.feed.includes('rbi.org')?'RBI':'ET'}: ${f.status.toLowerCase().replaceAll('_',' ')}`).join(' · ')} · ${news.fetched_at?`Fetched ${dateTime(news.fetched_at)}`:'Waiting for collection'}${news.risk!=='LOW'?' · New-position readiness blocked':''}`:"News worker not connected";
  $("strategy-priority").textContent=`${c?.slot?.new_entry_blocked?`${c.slot.strategy} occupies the slot; new entries blocked.`:'One slot shared by all engine strategies.'} Everyday carries retain ownership. Swing 18:45 is research-only, outside the 18:15 cutoff. Manual trades stay protected.`;
  const expiry=c?.expiry_check;
  $("expiry-status").textContent=expiry?Object.entries(expiry).map(([index,e])=>`${index}: ${e.is_expiry_day===true?"expiry — skip":e.is_expiry_day===false?"non-expiry":"expiry unverified"}`).join(" · "):"Expiry dates must be confirmed from Groww";
  $("levels").innerHTML=["NIFTY","SENSEX"].map(index=>{const level=c?.levels?.[index];return `<div class="level-item"><strong>${index}</strong><span>Support <b>${level?.support?.length?level.support.map(v=>numeric.format(v)).join(" · "):"—"}</b></span><span>Resistance <b>${level?.resistance?.length?level.resistance.map(v=>numeric.format(v)).join(" · "):"—"}</b></span><small>${level?`Updated ${escapeHTML(dateTime(level.updated_at))}${level.status==='STALE_RESEARCH'?' · stale research':''}`:"Waiting for 12:55 JST and fresh evidence"}</small></div>`;}).join("");
  const latest=c?.requests?.find(r=>r.rule);
  $("rule-details").hidden=!latest;$("rule-json").textContent=latest?JSON.stringify(latest.rule,null,2):"";
  $("control-note").textContent=c?.analysis_error?"Codex research needs attention. Review the local verification status.":`Manual and unknown trades are protected. ${c?.blockers?.includes("ATM_HEDGE_PAYOFF_REVIEW_REQUIRED")?"Historical ATM policy needs payoff review. ":""}Live entry and broker SL synchronization remain blocked until the Oracle executor and stop parameters are verified.`;
  $("analysis-status").textContent=(c?.requests?.length?c.requests.map(r=>r.status.toLowerCase()).join(" · "):"No barrier requests yet")+(c?.analysis_budget?` · Codex ${c.analysis_budget.used}/${c.analysis_budget.limit} attempts${c.analysis_budget.status==='DAILY_CAP_REACHED'?' — daily cap reached':''}`:'');
  $("trailing-status").textContent=c?.trailing?.plans?`${c.trailing.plans} trailing SL update proposal(s) · Oracle synchronization not deployed`:"No verified engine trailing stops · manual trades protected";
}
function renderAccount() {
  const a=data.account;
  setAmount("today-pnl",a.today_pnl_inr,true); $("today-return").textContent=percent(a.today_return_pct); tone($("today-return"),a.today_return_pct);
  setAmount("realized",a.realized_today_inr,true); setAmount("unrealized",a.unrealized_inr,true); $("trade-count").textContent=a.trade_count_today??"—";
  const funds=data.funds;
  setAmount("capital",data.demo?a.capital_inr:funds?.clear_cash_inr);
  setAmount("used-margin",data.demo?a.used_margin_inr:funds?.total_margin_used_inr);
  setAmount("available-margin",data.demo?a.available_margin_inr:funds?.option_sell_available_inr);
  setAmount("option-buy-money",data.demo?a.available_margin_inr:funds?.option_buy_available_inr);
  setAmount("collateral-money",data.demo?0:funds?.collateral_available_inr);
  $("funds-status").textContent=data.demo?"Sample cash · design preview":funds?.status==='AVAILABLE'?`Clear cash · Groww ${dateTime(funds.received_at)} · refresh 30s`:funds?.status==='STALE'?"Groww money is stale · waiting for a new read":"Groww money unavailable · amounts stay unknown";
  $("account-status").textContent=data.demo?"Sample results · design preview":a.status==="NOT_CONNECTED"?"Trade ledger not connected":a.status==="STALE_LEDGER"?"Ledger is from an earlier trading day":a.status==="INVALID_LEDGER"?"Trade ledger needs review":"Owner-reviewed ledger · after costs";
  setAmount("chart-pnl",a.today_pnl_inr,true); setAmount("portfolio-value",a.portfolio_value_inr);
  if(a.pnl_series?.length>=2) chart($("pnl-chart"),a.pnl_series,{area:true,pnl:true,label:"Today's net profit and loss"});
  else emptyChart($("pnl-chart"),"No P&L history yet","Connect your recorded trade results to see this chart.");
  if(a.portfolio_series?.length>=2) chart($("portfolio-chart"),a.portfolio_series,{color:"#fb861c",area:true,label:"Reviewed portfolio value"});
  else emptyChart($("portfolio-chart"),"No portfolio history yet","Your account values are never estimated from index moves.");
  $("strategy-rows").innerHTML=data.strategies.map((s,i)=>`<tr><td><div class="strategy-info ${known(s.net_pnl_inr)&&s.net_pnl_inr<0?"negative":""}"><span class="strategy-number">${i+1}</span><div class="strategy-content"><div class="strategy-name">${escapeHTML(s.name)}</div><p class="strategy-description">${escapeHTML(s.description)}</p><div class="outcome-track" role="img" aria-label="${escapeHTML(s.name)}: ${s.closed_trades?`${s.non_loss_pct.toFixed(1)}% non-losing, ${s.loss_pct.toFixed(1)}% losing, ${s.closed_trades} closed trades`:"No results"}"><span class="profit-segment" style="width:${s.non_loss_pct??0}%"></span><span class="loss-segment" style="width:${s.loss_pct??0}%"></span></div><span class="outcome-caption">${s.closed_trades?`${s.non_loss_pct.toFixed(0)}% non-loss · ${s.loss_pct.toFixed(0)}% loss · ${s.closed_trades} trades`:"No results"}</span></div></div></td><td class="${known(s.return_pct)?s.return_pct<0?"negative":"positive":""}">${percent(s.return_pct)}</td><td class="${known(s.net_pnl_inr)?s.net_pnl_inr<0?"negative":"positive":""}">${signed(s.net_pnl_inr)}</td></tr>`).join("");
}
function render() {
  if(!data) return;
  renderIndices(); renderOptions(); renderAccount(); renderControl();
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
  const markets=specs.map(([index,price,change])=>({index,price,change,change_pct:change/(price-change)*100,high:price+74,low:price-210,previous:price-change,series:series(price-change,change),options:[0,1].map((n)=>({symbol:`SAMPLE-${index}-${n}-CE`,index,type:"CE",side:n?"BUY":"SELL",order_status:"POSITION",quantity:65,protective_levels_status:"AVAILABLE",price_levels:[{kind:"ENTRY",price:[220,100][n],quantity:65},{kind:"SL",price:[260,65][n],quantity:65},{kind:"TARGET",price:[130,150][n],quantity:65}],exchange:index==="SENSEX"?"BSE":"NSE",expiry:index==="SENSEX"?"2026-10-08":"2026-10-06",strike:Math.ceil(price/100)*100+n*(index==="SENSEX"?800:400),last_price:[198,82][n],bid:[197,81][n],ask:[199,83][n],series:series([245,120][n],[-47,-38][n]),series_status:"SAMPLE"}))}));
  const rows=[{id:"everyday",name:"Everyday hedged call",description:"13:15 JST · NIFTY +500 / SENSEX +1,000",closed_trades:8,non_loss_pct:100,loss_pct:0,net_pnl_inr:8450,return_pct:28.2},{id:"late_session",name:"Late-session decay",description:"17:45–18:45 JST · Hedged calls",closed_trades:10,non_loss_pct:60,loss_pct:40,net_pnl_inr:-3200,return_pct:-12.8},{id:"swing",name:"Swing call spread",description:"Overnight · 10 / 20 strike study",closed_trades:12,non_loss_pct:75,loss_pct:25,net_pnl_inr:12600,return_pct:18},{id:"expiry_reversal",name:"Expiry reversal",description:"SENSEX · 18:50 JST onward",closed_trades:5,non_loss_pct:80,loss_pct:20,net_pnl_inr:6100,return_pct:24.4}];
  const pnl=series(0,24850,40).map((r,i)=>({...r,value:r.value-(i>2&&i<12?Math.sin((i-2)/10*Math.PI)*10000:0)}));
  return {demo:true,orders_status:"AVAILABLE",positions_status:"AVAILABLE",as_of:new Date(start+71*300000).toISOString(),freshness:"SAMPLE",markets,strategies:rows,account:{status:"SAMPLE",capital_inr:150000,portfolio_value_inr:218450,used_margin_inr:112000,available_margin_inr:38000,margin_utilization_pct:74.67,today_pnl_inr:24850,today_return_pct:16.57,realized_today_inr:22300,unrealized_inr:2550,trade_count_today:8,pnl_series:pnl,portfolio_series:series(190000,28450,25)}};
}
$("refresh").addEventListener("click",refresh);
$("settings").addEventListener("click",()=>$("settings-dialog").showModal());
$("settings-dialog").addEventListener("click",event=>{if(event.target===$("settings-dialog")) {const r=$("settings-dialog").getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom) $("settings-dialog").close();}});
$("timezone").value=timeZone;
$("timezone").addEventListener("change",event=>{timeZone=event.target.value;try{localStorage.setItem("trading-timezone",timeZone);}catch{}render();});
$("auto-refresh").addEventListener("change",event=>{autoRefresh=event.target.checked;if(autoRefresh) refresh();});
$("chart-detail").addEventListener("change",event=>{chartDetail=event.target.value;render();});
$("demo-toggle").addEventListener("change",event=>{demo=event.target.checked;contracts={buy:null,sell:null};data=demo?sampleData():liveData;render();if(!demo&&autoRefresh) refresh();});
for(const tab of document.querySelectorAll(".tab")) tab.addEventListener("click",()=>{selectedIndex=tab.dataset.index;try{localStorage.setItem("trading-index",selectedIndex);}catch{}contracts={buy:null,sell:null};renderOptions(true);});
for(const side of ["buy","sell"]) $(side+"-option").addEventListener("change",event=>{contracts[side]=event.target.value||null;const market=data.markets.find(m=>m.index===selectedIndex);drawOption(side,market?.options.find(o=>o.symbol===contracts[side]));});
load().then(()=>refresh());
setInterval(async()=>{if(!document.hidden){await load();if(autoRefresh&&!demo&&Date.now()-lastRequest>=4500) await refresh();}},5000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&autoRefresh&&!demo) refresh();});
