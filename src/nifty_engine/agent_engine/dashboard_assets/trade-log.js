"use strict";
const $=id=>document.getElementById(id);
let audit=null;
const timeFmt=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Tokyo',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const fmt=v=>Number.isFinite(v)?new Intl.NumberFormat('en-IN',{maximumFractionDigits:2}).format(v):'—';
function time(v){const n=Date.parse(v);return Number.isFinite(n)?timeFmt.format(n):'—';}
function node(tag,text,klass){const n=document.createElement(tag);n.textContent=text??'—';if(klass)n.className=klass;return n;}
function detail(row){
 if(row.kind==='ORDER')return ['Order '+(row.side||''),row.symbol,row.quantity!=null?'Qty '+fmt(row.quantity):null].filter(Boolean);
 if(row.kind==='PROTECTION')return ['GTT protection',row.symbol].filter(Boolean);
 if(row.kind==='BASKET')return [row.strategy,row.index,row.spread,row.expiry].filter(Boolean);
 if(row.kind==='EVALUATION')return ['NIFTY '+(row.NIFTY?.status||'—'),'SENSEX '+(row.SENSEX?.status||'—')];
 return [row.strategy,row.index,row.symbol].filter(Boolean);
}
function readable(row){
 if(row.kind==='ORDER')return [row.price_inr!=null?'Limit ₹'+fmt(row.price_inr):null,row.average_fill_inr!=null?'Avg fill ₹'+fmt(row.average_fill_inr):null].filter(Boolean).join(' · ')||'—';
 if(row.kind==='PROTECTION')return [row.trigger_price_inr!=null?'Trigger ₹'+fmt(row.trigger_price_inr):null,row.limit_price_inr!=null?'Limit ₹'+fmt(row.limit_price_inr):null].filter(Boolean).join(' · ')||'—';
 if(row.kind==='BASKET')return row.exit_reason||'—';
 if(row.kind==='EVALUATION')return ['NIFTY '+(row.NIFTY?.reasons||[]).join(', '),'SENSEX '+(row.SENSEX?.reasons||[]).join(', ')].join(' | ');
 return row.stage||'—';
}
function status(row){
 if(row.kind==='EVALUATION')return [row.NIFTY?.regime,row.SENSEX?.regime].filter(Boolean).join(' / ')||'CHECKED';
 if(row.kind==='STRATEGY')return 'Normal Theta '+(row.normal_theta?'On':'Off')+' · Research '+(row.research?'On':'Off');
 return row.status||'UNKNOWN';
}
function qty(row){return row.quantity!=null?fmt(row.filled_quantity??0)+' / '+fmt(row.quantity):'—';}
function render(){
 const all=audit?.events||[],selected=$('filter').value;
 const rows=selected==='ALL'?all:all.filter(r=>r.kind===selected);
 $('events').replaceChildren();
 for(const r of rows){
   const tr=document.createElement('tr'),parts=[time(r.at),r.kind,status(r),detail(r).join(' · ')||'—',qty(r),readable(r),r.evidence||r.source||'JOURNAL'];
   for(let i=0;i<7;i++){const td=node('td',parts[i],i===2?'value':'');if(i===1){td.textContent='';td.append(node('span',parts[i],'event-type event-'+r.kind));}tr.append(td);}
   $('events').append(tr);
 }
 if(!rows.length){const tr=document.createElement('tr');const td=node('td',all.length?'No events match this filter.':'No recorded algorithm trades yet. Events appear after the engine records state changes.','empty');td.colSpan=7;tr.append(td);$('events').append(tr);}
 $('count').textContent=String(all.length);
 $('last-event').textContent=all[0]?.kind||'No events';
 $('last-time').textContent=all[0]?time(all[0].at)+' JST':'No committed audit events';
 $('export').disabled=!all.length;
}
async function load(){
 $('reload').disabled=true;
 try {
   const response=await fetch('/api/trade-log',{cache:'no-store'});
   if(!response.ok)throw Error('JOURNAL_UNREACHABLE');
   const payload=await response.json();
   if(payload.format!=='trading-audit-v1'||payload.read_only!==true||payload.broker_writes!==false)throw Error('AUDIT_CONTRACT_INVALID');
   audit=payload;
   $('connection').textContent=payload.status==='AVAILABLE'?'Connected':payload.status==='AUDIT_NOT_DEPLOYED'?'Awaiting Oracle update':payload.status;
   $('source').textContent=payload.source||'Read-only local viewer';
   $('health').textContent=payload.status==='AVAILABLE'?'Showing committed engine transitions, newest first. No broker action is issued by this page.':'Audit feed is unavailable until the Oracle observer has the audit module installed.';
   render();
 } catch(e){$('connection').textContent='Unavailable';$('health').textContent='Could not read the audit safely. Last visible events may be stale.';}
 finally{$('reload').disabled=false;}
}
function csvCell(x){const s=String(x??'');return '"'+( /^[=+@\-\t\r]/.test(s)?"'":'')+s.replaceAll('"','""')+'"';}
$('export').addEventListener('click',()=>{
 const rows=audit?.events||[];if(!rows.length)return;
 const header=['sequence','timestamp_utc','event','status','strategy_or_contract','quantity_and_fill','details','evidence'];
 const records=rows.map(r=>[r.id,r.at,r.kind,status(r),detail(r).join(' | '),qty(r),readable(r),r.evidence||'JOURNAL']);
 const content='\uFEFF'+[header,...records].map(row=>row.map(csvCell).join(',')).join('\r\n');
 const url=URL.createObjectURL(new Blob([content],{type:'text/csv;charset=utf-8'}));
 const a=document.createElement('a');a.href=url;a.download='options-trader-audit-'+new Date().toISOString().slice(0,10)+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('filter').addEventListener('change',render);
$('reload').addEventListener('click',load);
load();
setInterval(()=>{if(!document.hidden)load();},15000);
