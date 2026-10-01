"""Deterministic visual EOD reports with CID PNG charts and a durable email outbox."""
from __future__ import annotations

import base64
import html
import json
import os
import re
import struct
import uuid
import zlib
from datetime import datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import IST, dumps, identity, stamp


def chart(points, width=620, height=160):
    """Small dependency-free PNG. Missing observations break, not interpolate, the line."""
    pixels = bytearray([248, 250, 252] * width * height)
    def dot(x, y, rgb):
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = bytes(rgb)
    known = [v for _, v in points if v is not None]
    low, high = (min(0, min(known)), max(0, max(known))) if known else (0, 1)
    span = max(high - low, 1)
    first, last = (points[0][0], points[-1][0]) if points else (0, 1)
    def ycoord(v):
        return int(height - 12 - (v - low) / span * (height - 24))
    for x in range(10, width - 10):
        dot(x, ycoord(0), (180, 190, 205))
    previous = None
    for at, value in points:
        if value is None:
            previous = None
            continue
        x = int(10 + (at - first) / max(last - first, 1) * (width - 20))
        y = ycoord(value)
        if previous is not None:
            old_x, old_y = previous
            steps = max(abs(x - old_x), abs(y - old_y), 1)
            for step in range(steps + 1):
                px, py = round(old_x + (x - old_x) * step / steps), round(old_y + (y - old_y) * step / steps)
                for dy in (-1, 0, 1):
                    dot(px, py + dy, (24, 110, 160))
        else:
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    dot(x + dx, y + dy, (24, 110, 160))
        previous = (x, y)
    raw = b"".join(b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3]) for y in range(height))
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def money(value):
    return "Unknown" if value is None else f"INR {value:,.2f}"


def build_report(store, day: str, now: datetime):
    start = datetime.combine(datetime.strptime(day, "%Y-%m-%d").date(), time(), IST)
    end = start + timedelta(days=1)
    # Read the large market/news payload once. Chart/accounting queries extract
    # only needed fields, so a month of quote snapshots is not loaded into RAM.
    rows = store.read("SELECT body FROM snapshots WHERE day=? ORDER BY at DESC,rowid DESC LIMIT 1", (day,))
    latest = json.loads(rows[0]["body"]) if rows else None
    p = latest["portfolio"] if latest and latest["portfolio"]["accounting_day"] == day else {}
    source = store.meta("source", ["NO SOURCE", "OBSERVE"])
    goal = store.meta("goal")
    jobs = store.read("SELECT reason,status,result,error FROM jobs WHERE created>=? AND created<? ORDER BY created", (start.timestamp(), end.timestamp()))
    events = store.read("SELECT kind,COUNT(*) AS count FROM events WHERE at>=? AND at<? GROUP BY kind", (start.timestamp(), end.timestamp()))
    months = store.read("SELECT day,body FROM (SELECT day,body,ROW_NUMBER() OVER (PARTITION BY day ORDER BY at DESC,rowid DESC) AS n FROM snapshots WHERE day>=? AND day<=?) WHERE n=1", (day[:7] + "-01", day))
    daily = {}
    for row in months:
        item = json.loads(row["body"])
        if item["portfolio"]["accounting_day"] == row["day"]:
            daily[row["day"]] = item["portfolio"]["realized_pnl"]
    month_recorded = sum(v for v in daily.values() if v is not None) if daily and all(v is not None for v in daily.values()) else None
    points = []
    marks = store.read("SELECT at,json_extract(body,'$.portfolio.realized_pnl') AS realized,json_extract(body,'$.portfolio.unrealized_pnl') AS unrealized FROM snapshots WHERE day=? AND json_extract(body,'$.portfolio.accounting_day')=? ORDER BY at,rowid", (day, day))
    for mark in marks:
        realized, unrealized = mark["realized"], mark["unrealized"]
        value = realized + unrealized if realized is not None and unrealized is not None else None
        points.append((mark["at"], value))
    png = chart(points)
    esc = lambda x: html.escape(str(x), quote=True)
    observed = latest["observed_at"] if latest else "NO OBSERVATIONS"
    age = str(max(0, int((now - stamp(observed)).total_seconds()))) + " seconds" if latest else "Unknown"
    status = latest["status"] if latest else "NO_DATA"
    realized, unrealized = p.get("realized_pnl"), p.get("unrealized_pnl")
    cards = [("Recorded realized P&L", money(realized)), ("Last recorded open P&L", money(unrealized)),
             ("Open positions", latest["portfolio"]["open_positions"] if latest else "Unknown"), ("Recorded entries", p.get("trades", "Unknown"))]
    card_html = "".join(f'<td width="50%" style="padding:12px;background:#eef3f8;border:4px solid white"><div style="font-size:12px;color:#546779">{esc(label)}</div><b style="font-size:23px">{esc(value)}</b></td>' for label, value in cards)
    cells = re.findall(r"<td.*?</td>", card_html)
    target_html = '<p>Monthly goal: not configured. No guaranteed return or automatic risk increase.</p>'
    if goal and goal["month"] == day[:7]:
        pct = max(0, min(100, 100 * month_recorded / goal["target_inr"])) if month_recorded is not None else 0
        target_html = f'<p>Recorded month realized: <b>{esc(money(month_recorded))}</b> / goal {esc(money(goal["target_inr"]))}</p><table width="100%"><tr><td width="{pct:.0f}%" bgcolor="#237ba0" height="10"></td><td bgcolor="#e2e8f0"></td></tr></table><p style="font-size:12px">Only {len(daily)} observed calendar days. Not a reconciled full-month return; open P&amp;L excluded. Target feasibility: INSUFFICIENT_EVIDENCE.</p>'
    decisions = []
    for row in jobs[-12:]:
        result = json.loads(row["result"]) if row["result"] else None
        summary = result["summary"] if result else row["error"] or "Pending / expired without a result"
        decisions.append(f'<tr><td style="padding:8px;border-bottom:1px solid #dde5ee">{esc(row["reason"])}<br><small>{esc(row["status"])}</small></td><td style="padding:8px;border-bottom:1px solid #dde5ee">{esc(summary)}</td></tr>')
    reasons = " / ".join(latest["reasons"]) if latest and latest["reasons"] else "No recorded reason; not evidence of a healthy feed."
    markets = "".join(f'<tr><td>{esc(index)}</td><td>{esc("Unknown" if row["spot"] is None else format(row["spot"], ",.2f") + " points")}</td><td>{esc(row["at"])}</td></tr>' for index, row in (latest or {}).get("markets", {}).items())
    news = "".join(f'<p><b>{esc(item["title"])}</b><br><small>{esc(item["source"])} | {esc(item["published_at"])}</small></p>' for item in (latest or {}).get("news", [])[-8:]) or '<p>No verified headline evidence was supplied. This does not mean no news occurred.</p>'
    chart_html = '<img alt="Recorded P&amp;L; gaps indicate missing marks" src="cid:daily-pnl" width="620" style="width:100%;height:auto">' if any(v is not None for _, v in points) else '<p>No priced observations: no performance chart is available.</p>'
    content = f'''<!doctype html><html><body style="margin:0;background:#e9eef4;font-family:Arial,sans-serif;color:#172d42"><table role="presentation" width="100%"><tr><td align="center"><table role="presentation" width="680" style="max-width:100%;background:white"><tr><td style="padding:26px;background:#172d42;color:white"><div style="font-size:12px;letter-spacing:2px">GROWING TRADER / {esc(source[1])}</div><h1 style="font-size:28px;margin-bottom:8px">Your market day, explained.</h1><div>{esc(day)} · India session date</div></td></tr><tr><td style="padding:22px"><p><b>{esc(status)}</b> · {esc(source[0])}</p><p style="font-size:12px">Last observation: {esc(observed)} · age at report: {esc(age)}. These are recorded values, not current executable marks.</p><table role="presentation" width="100%"><tr>{''.join(cells[:2])}</tr><tr>{''.join(cells[2:])}</tr></table><h2 style="font-size:18px">Recorded intraday P&amp;L</h2>{chart_html}<p style="font-size:12px">Realized plus recorded open P&amp;L, INR; left-to-right observation time. Missing marks break the line. No observations means no performance chart.</p><h2 style="font-size:18px">Monthly progress</h2>{target_html}<h2 style="font-size:18px">Market observations</h2><table width="100%" style="font-size:12px">{markets or '<tr><td>No instrument data recorded.</td></tr>'}</table><h2 style="font-size:18px">What Codex reviewed</h2><p>{len(jobs)} analysis jobs; last {min(12,len(jobs))} shown. Proposals are not executed strategy changes.</p><table width="100%" style="font-size:13px">{''.join(decisions) or '<tr><td>No Codex runs recorded.</td></tr>'}</table><h2 style="font-size:18px">Risk / operations</h2><p>{esc(reasons)}</p><p>{esc(" / ".join(str(e["count"]) + " " + e["kind"] for e in events) or "No operational events recorded.")}</p><h2 style="font-size:18px">News evidence</h2>{news}<hr><p style="font-size:12px;color:#546779">No real-money orders are available in this agent engine. PAPER and REPLAY figures are simulations, not verified broker profits. Costs follow the publishing ledger; no extra fees or income-tax deductions are invented. Unpriced or pending positions remain unresolved. Generated independently of Codex availability.</p></td></tr></table></td></tr></table></body></html>'''
    text = f"Growing Trader {source[1]} {day}\nStatus: {status}\nObserved: {observed}\nRealized: {money(realized)}\nOpen P&L: {money(unrealized)}\nOpen positions: {p.get('open_positions', 'Unknown')}\nAnalysis jobs: {len(jobs)}\nReasons: {reasons}\nSimulation / observation only. No guaranteed profit."
    return {"mode": source[1], "day": day, "observed_at": observed,
            "mail": {"subject": f"Growing Trader [{source[1]}] · {day} · {status}", "html": content, "text": text,
            "attachments": [{"filename": "daily-pnl.png", "content_id": "daily-pnl", "content": base64.b64encode(png).decode()}]}}


def save_preview(bundle, directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    mail = bundle["mail"]
    image = base64.b64decode(mail["attachments"][0]["content"])
    (root / "daily-pnl.png").write_bytes(image)
    (root / "report.html").write_text(mail["html"].replace("cid:daily-pnl", "daily-pnl.png"), encoding="utf-8")
    (root / "report.json").write_text(dumps(bundle), encoding="utf-8")
    message = EmailMessage()
    message["Subject"] = mail["subject"]
    message.set_content(mail["text"])
    message.add_alternative(mail["html"], subtype="html")
    message.get_payload()[1].add_related(image, maintype="image", subtype="png", cid="<daily-pnl>")
    (root / "report.eml").write_bytes(message.as_bytes())


def queue_report(store, bundle):
    report_id = identity([store.meta("source"), bundle["day"], "eod-v1"])
    with store.transaction() as db:
        db.execute("INSERT OR IGNORE INTO reports(id,day,payload) VALUES(?,?,?)", (report_id, bundle["day"], dumps(bundle)))
    return report_id


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("email redirect refused")


def resend(payload, key, report_id):
    request = Request("https://api.resend.com/emails", data=dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key, "Idempotency-Key": report_id,
        "User-Agent": "GrowingTrader-Headless/1.0"})
    with build_opener(NoRedirect).open(request, timeout=20) as response:
        raw = response.read(64001)
    if len(raw) > 64000:
        raise ValueError("oversized mail response")
    result = json.loads(raw)
    if not isinstance(result.get("id"), str) or not result["id"]:
        raise ValueError("missing provider receipt")
    return result["id"]


def receipt_status(provider_id, key):
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", provider_id):
        raise ValueError("invalid provider receipt")
    request = Request("https://api.resend.com/emails/" + provider_id,
                      headers={"Authorization": "Bearer " + key, "User-Agent": "GrowingTrader-Headless/1.0"})
    with build_opener(NoRedirect).open(request, timeout=20) as response:
        raw = response.read(64001)
    if len(raw) > 64000:
        raise ValueError("oversized provider receipt")
    return json.loads(raw)


def reconcile_receipt(store, report_id, now, *, fetch=receipt_status):
    rows = store.read("SELECT status,provider_id,payload FROM reports WHERE id=?", (report_id,))
    if not rows or rows[0]["status"] != "ACCEPTED":
        return {"status": "NO_ACCEPTED_RECEIPT", "inbox_verified": False}
    row = rows[0]
    result = {"status": "ACCEPTED", "provider_id": row["provider_id"],
              "checked_at": now.isoformat(), "inbox_verified": False}
    try:
        receipt = fetch(row["provider_id"], os.environ["RESEND_API_KEY"])
        envelope = json.loads(row["payload"])["mail"]
        if receipt.get("id") != row["provider_id"] or receipt.get("to") != envelope["to"] or receipt.get("from") != envelope["from"]:
            raise ValueError("provider receipt does not match frozen envelope")
        event = receipt.get("last_event")
        if event not in ("sent", "delivered", "delivery_delayed", "bounced", "complained", "failed", "opened", "clicked", "queued", "scheduled", "suppressed"):
            raise ValueError("unknown delivery event")
        result.update(provider_event=event, message_id=receipt.get("message_id"),
            status="RECIPIENT_SERVER_ACCEPTED" if event in ("delivered", "opened", "clicked") else "PROVIDER_" + event.upper())
    except Exception as exc:
        result["reconciliation_error"] = type(exc).__name__
    store.set_meta("receipt:" + report_id, result)
    return result


def deliver_once(store, now, *, send=resend):
    key, sender, recipient = (os.getenv(name, "").strip() for name in ("RESEND_API_KEY", "TRADING_REPORT_FROM", "TRADING_REPORT_TO"))
    if not all((key, sender, recipient)):
        return "MAIL_NOT_CONFIGURED"
    if any(c in sender + recipient for c in "\r\n") or "@" not in sender or "@" not in recipient:
        return "MAIL_ADDRESS_INVALID"
    token = uuid.uuid4().hex
    with store.transaction() as db:
        db.execute("UPDATE reports SET status='QUEUED' WHERE status='SENDING' AND lease<=?", (now.timestamp(),))
        row = db.execute("SELECT * FROM reports WHERE status='QUEUED' AND next_attempt<=? ORDER BY day LIMIT 1", (now.timestamp(),)).fetchone()
        if row is None:
            return "IDLE"
        if row["day"] < (now.astimezone(IST).date() - timedelta(days=7)).isoformat():
            db.execute("UPDATE reports SET status='REVIEW_REQUIRED' WHERE id=?", (row["id"],))
            return "REVIEW_REQUIRED"
        if (row["first_attempt"] is not None and now.timestamp() - row["first_attempt"] >= 23 * 3600) or row["attempts"] >= 8:
            db.execute("UPDATE reports SET status='REVIEW_REQUIRED' WHERE id=?", (row["id"],))
            return "REVIEW_REQUIRED"
        bundle = json.loads(row["payload"])
        if bundle["mode"] == "REPLAY":
            db.execute("UPDATE reports SET status='PREVIEW_ONLY' WHERE id=?", (row["id"],))
            return "PREVIEW_ONLY"
        payload = bundle["mail"]
        # Freeze envelope on the first attempt. Retries reuse identical content.
        payload.setdefault("from", sender)
        payload.setdefault("to", [recipient])
        db.execute("UPDATE reports SET payload=?,status='SENDING',token=?,lease=?,attempts=attempts+1,first_attempt=COALESCE(first_attempt,?) WHERE id=?", (dumps(bundle), token, now.timestamp() + 60, now.timestamp(), row["id"]))
    try:
        receipt = send(payload, key, row["id"])
        with store.transaction() as db:
            db.execute("UPDATE reports SET status='ACCEPTED',provider_id=?,token=NULL WHERE id=? AND token=?", (receipt, row["id"], token))
        return "ACCEPTED"  # Provider acceptance is not proof of inbox delivery.
    except Exception as exc:
        with store.transaction() as db:
            db.execute("UPDATE reports SET status='QUEUED',error=?,token=NULL,next_attempt=? WHERE id=? AND token=?", (type(exc).__name__, now.timestamp() + min(3600, 60 * 2 ** row["attempts"]), row["id"], token))
        return "RETRY_PENDING"
