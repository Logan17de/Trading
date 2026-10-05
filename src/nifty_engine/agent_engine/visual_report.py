"""Compact visual owner email from actual observations; separate durable outbox."""
import base64
import copy
import html
from datetime import datetime, time

from .contracts import dumps, identity, stamp
from .pc_control import JST
from .reporting import chart, deliver_once, reconcile_receipt


def build(view, day):
    view=copy.deepcopy(view)
    for market in view.get("markets",[]):
        market["series"]=[r for r in market.get("series",[]) if stamp(r["at"]).astimezone(JST).date().isoformat()==day]
        if view.get("freshness")!="RECENT":
            for k in ("price","change","change_pct"):market[k]=None
    esc=lambda v:html.escape(str(v))
    money=lambda v:"—" if v is None else f"₹{v:,.2f}"
    level=lambda v:"—" if v is None else f"{v:,.2f}"
    attachments=[]
    def image(name, rows, color, width=620, height=170):
        points=[]
        for r in rows:
            at=stamp(r["at"]).timestamp()
            if points and at-points[-1][0]>20:points.append((at-.001,None))
            points.append((at,r.get("value")))
        if not any(v is not None for _,v in points):return '<div style="padding:35px;color:#718096">No recorded prices</div>'
        png=chart(points,width,height,color,zero_baseline=not name.startswith("index-"))
        attachments.append({"filename":name+".png","content_id":name,"content":base64.b64encode(png).decode()})
        first=stamp(rows[0]["at"]).astimezone(JST).strftime("%H:%M")
        last=stamp(rows[-1]["at"]).astimezone(JST).strftime("%H:%M")
        values=[v for _,v in points if v is not None]
        fmt=level if name.startswith("index-") else money
        unit=" pts" if name.startswith("index-") else ""
        return f'<img src="cid:{name}" width="{width}" style="width:100%;height:auto" alt="{name}"><div style="font-size:10px;color:#587287">{first}–{last} JST · {fmt(min(values))}–{fmt(max(values))}{unit}</div>'
    def tile(title,value,sub="",color="#18334b"):
        return f'<td width="50%" style="padding:16px;background:#f4faf8;border:4px solid white;border-radius:12px"><div style="font-size:12px;color:#587287">{esc(title)}</div><div style="font-size:27px;font-weight:bold;color:{color}">{esc(value)}</div><div style="font-size:11px;color:#587287">{esc(sub)}</div></td>'
    pnl=view.get("broker_pnl",{}); buckets=pnl.get("buckets",{}); funds=view.get("funds",{})
    series=[r for r in pnl.get("series",[]) if stamp(r["at"]).astimezone(JST).date().isoformat()==day]
    cards=tile("Today · before charges",money(pnl.get("total_inr")),f'Realised {money(pnl.get("realized_inr"))} · Open {money(pnl.get("unrealized_inr"))} · Closed contracts {pnl.get("closed_contracts") if pnl.get("closed_contracts") is not None else "—"}')
    cards+=tile("Clear cash",money(funds.get("clear_cash_inr")),funds.get("received_at") or "Unknown")
    split=tile("Self",money(buckets.get("self",{}).get("total_inr")),"Non-journal trades", "#007da6")
    split+=tile("Algo",money(buckets.get("algo",{}).get("total_inr")),"Exact journal ownership", "#069e62")
    images=""
    for b,color in (("self",(0,125,166)),("algo",(6,158,98)),("unassigned",(215,126,30))):
        if b=="unassigned" and not buckets.get(b,{}).get("contracts"):continue
        rows=[{"at":r["at"],"value":r.get(b)} for r in series]
        images+=f'<div style="margin:16px 0"><b style="font-size:13px">{esc(b.title())} · observed P&amp;L</b>'+image("pnl-"+b,rows,color)+"</div>"
    markets=""; positions=[]
    for m in view.get("markets",[]):
        markets+=f'<td width="33%" style="padding:12px;background:#f8fbfc;border:3px solid white"><b>{esc(m["index"])}</b><div style="font-size:21px;margin:8px 0">{esc(level(m.get("price")))}</div>'+image("index-"+m["index"],m.get("series",[]),(6,158,98),200,90)+"</td>"
        for o in m.get("options",[]):
            entry=next((p.get("price") for p in o.get("price_levels",[]) if p.get("kind")=="ENTRY"),None)
            owner="Algo" if o.get("ownership")=="ENGINE_VERIFIED" else "Self / protected"
            protection=" · ".join(f'{p["kind"]} {money(p.get("price"))}' for p in o.get("price_levels",[]) if p.get("kind") in ("SL","TARGET")) or "Protection unknown"
            positions.append(f'<tr><td style="padding:10px;border-bottom:1px solid #e3eeeb"><b>{esc(o["symbol"])}</b><br><small>{esc(o["side"])} × {esc(o["quantity"])} · {esc(owner)}</small></td><td>{esc(money(entry))}<br><small>Entry</small></td><td>{esc(money(o.get("last_price")))}<br><small>Last</small></td><td>{esc(money(o.get("open_pnl_inr")))}<br><small>{esc(protection)}</small></td></tr>')
    strategies=""
    for s in view.get("strategies",[]):
        pct=s.get("non_loss_pct")
        bar=f'<div style="background:#edf2f4;height:7px;width:150px"><div style="background:#08b75d;height:7px;width:{max(0,min(100,pct))}%"></div></div>' if pct is not None else '<small>Results unknown</small>'
        strategies+=f'<tr><td style="padding:12px"><b>{esc(s["name"])}</b>{bar}</td><td>{esc("—" if pct is None else str(round(pct,1))+"% non-loss")}</td><td>{esc(money(s.get("net_pnl_inr")))}</td></tr>'
    control=view.get("control",{}); algo=control.get("algo",{}); news=control.get("news",{})
    cells=tile("Used margin",money(funds.get("total_margin_used_inr")))+tile("Option sell / buy",money(funds.get("option_sell_available_inr"))+" / "+money(funds.get("option_buy_available_inr")))
    status=f'Algo {"ON · blocked" if algo.get("desired_enabled") else "OFF"} · Broker writes OFF · News {news.get("risk","UNKNOWN")}'
    body=f'''<!doctype html><html><body style="margin:0;background:#edf6f4;font-family:Arial,sans-serif;color:#18334b"><table role="presentation" width="100%"><tr><td align="center"><table role="presentation" width="680" style="max-width:100%;background:white"><tr><td style="padding:24px;background:#18334b;color:white"><div style="font-size:25px;font-weight:bold">Options Trader</div><div style="margin-top:8px;font-size:13px">{esc(day)} · 19:30 JST report</div></td></tr><tr><td style="padding:16px"><table width="100%"><tr>{cards}</tr><tr>{split}</tr><tr>{cells}</tr></table><div style="padding:10px;background:#f4faf8;font-size:12px">{esc(status)}<br>Observed: {esc(view.get("as_of") or "Unknown")}</div>{images}<table width="100%"><tr>{markets}</tr></table><h2 style="font-size:16px">Open positions</h2><table width="100%" style="font-size:12px">{''.join(positions) or '<tr><td>No confirmed open positions</td></tr>'}</table><h2 style="font-size:16px">Strategies · net results</h2><table width="100%" style="font-size:12px">{strategies}</table><div style="margin-top:20px;color:#718096;font-size:10px">Gross P&amp;L excludes charges. Net results require the reviewed ledger. Gaps = missing reads. Manual positions protected. No guaranteed profit.</div></td></tr></table></td></tr></table></body></html>'''
    return {"mode":"OBSERVE","day":day,"observed_at":view.get("as_of"),"mail":{
        "subject":"Options Trader · "+day,"html":body,"text":"Options Trader · "+day+"\n"+status+"\nToday gross: "+money(pnl.get("total_inr")),"attachments":attachments}}


class DailyMail:
    def __init__(self,store):self.store=store

    def tick(self,view,now, *, send=None,fetch=None):
        local=now.astimezone(JST);day=local.date().isoformat()
        # Begin one minute before the owner's deadline; provider/inbox delays
        # still cannot be guaranteed. Freeze only when the due attempt is made.
        if local.time()<time(19,29):return {"status":"SCHEDULED_BY_1930_JST","provider_accepted":False,"inbox_verified":False}
        report_id=identity([day,"oracle-visual-eod-v1"])
        with self.store.transaction() as db:
            if not db.execute("SELECT 1 FROM reports WHERE id=?",(report_id,)).fetchone():
                db.execute("INSERT INTO reports(id,day,payload) VALUES(?,?,?)",(report_id,day,dumps(build(view,day))))
        delivery=deliver_once(self.store,now,**({"send":send} if send else {}))
        rows=self.store.read("SELECT status FROM reports WHERE id=?",(report_id,))
        receipt={"status":delivery,"provider_accepted":bool(rows and rows[0]["status"]=="ACCEPTED"),"inbox_verified":False}
        if receipt["provider_accepted"]:
            checked=self.store.meta("receipt:"+report_id,{})
            if not checked.get("checked_at") or (now-stamp(checked["checked_at"])).total_seconds()>=300:
                checked=reconcile_receipt(self.store,report_id,now,**({"fetch":fetch} if fetch else {}))
            receipt.update(status=checked.get("status","ACCEPTED"),recipient_server_accepted=checked.get("status")=="RECIPIENT_SERVER_ACCEPTED")
        self.store.set_meta("visual-mail-"+day,receipt)
        return receipt
