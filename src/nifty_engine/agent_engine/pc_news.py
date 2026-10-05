"""Independent, bounded PC news evidence worker; never an order capability."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from .contracts import dumps, identity, stamp
from .news import fresh, https_host, parse_feed, request_bytes, validate_assessment

FEEDS = ("https://rbi.org.in/pressreleases_rss.xml",
         "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms")
HOSTS = {https_host(url) for url in FEEDS}


def collect(now, *, fetch=request_bytes):
    articles, feeds = {}, []
    for url in FEEDS:
        try:
            rows = [r for r in parse_feed(fetch(url), url, now)
                    if https_host(r["url"]) == https_host(url)][:30]
            articles.update({r["id"]: r for r in rows})
            feeds.append({"feed": url, "status": "FRESH" if rows else "MISSING_OR_STALE", "count": len(rows)})
        except Exception as exc:
            feeds.append({"feed": url, "status": "UNAVAILABLE", "error_type": type(exc).__name__})
    # Retain evidence from both mandatory feeds even when one publisher has more headlines.
    evidence = sorted(articles.values(), key=lambda r: r["published_at"], reverse=True)
    selected = []
    for host in sorted(HOSTS):
        selected.extend([r for r in evidence if r["source"] == host][:15])
    value = {"format": "trusted-news-evidence-v1", "fetched_at": now.isoformat(),
             "risk": "UNKNOWN", "assessment": "UNASSESSED_REQUIRES_ANALYST",
             "feeds": feeds, "articles": sorted(selected, key=lambda r: r["published_at"], reverse=True)}
    value["fingerprint"] = identity({k: value[k] for k in ("feeds", "articles")})
    return value


def context(raw, now):
    """Whitelist local evidence before it reaches Codex or the browser."""
    missing = {"risk": "UNKNOWN", "status": "NEWS_MISSING_OR_INVALID", "fetched_at": None,
               "fingerprint": None, "feeds": [], "articles": []}
    try:
        if (raw["format"] != "trusted-news-evidence-v1" or len(raw["articles"]) > 30
                or raw["fingerprint"] != identity({k:raw[k] for k in ("feeds", "articles")})):
            return missing
        feeds = [{k: r[k] for k in ("feed", "status")} for r in raw["feeds"]]
        if {r["feed"] for r in feeds} != set(FEEDS) or len(feeds) != 2:
            return missing
        rows = []
        for row in raw["articles"]:
            if (row["source"] not in HOSTS or https_host(row["url"]) != row["source"]
                    or not isinstance(row["id"], str) or not 1 <= len(row["id"]) <= 64
                    or not isinstance(row["title"], str) or not 1 <= len(row["title"]) <= 500
                    or not fresh(stamp(row["published_at"]), now, 86400)):
                continue
            rows.append({k: row[k] for k in ("id", "title", "url", "source", "published_at")})
        result = {"risk": "UNKNOWN", "status": "UNASSESSED_REQUIRES_ANALYST",
                  "fetched_at": raw["fetched_at"], "fingerprint": raw["fingerprint"], "feeds": feeds,
                  "articles": rows}
        if not fresh(stamp(raw["fetched_at"]), now, 180):
            result["status"] = "NEWS_FETCH_STALE"
        elif (not all(r["status"] == "FRESH" for r in feeds)
                or {r["source"] for r in rows} != HOSTS):
            result["status"] = "MANDATORY_NEWS_MISSING_OR_STALE"
        elif raw.get("assessed_at") and fresh(stamp(raw["assessed_at"]), now, 180):
            assessment = validate_assessment({k: raw[k] for k in ("risk", "summary", "evidence_ids")}, rows)
            result.update(assessment, assessed_at=raw["assessed_at"], status="VALIDATED_ASSESSMENT")
        elif raw.get("assessed_at"):
            result["status"] = "NEWS_ASSESSMENT_STALE"
        return result
    except (KeyError, ValueError, TypeError, OverflowError):
        return missing


class PcNews:
    def __init__(self, root):
        self.path = Path(root) / ".agent-state/pc-news.json"
        self.lock = threading.RLock()

    def _read(self):
        try:
            if self.path.stat().st_size > 100_000:
                return {}
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def snapshot(self, now):
        with self.lock:
            return context(self._read(), now)

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(dumps(value), encoding="utf-8")
        os.replace(temporary, self.path)

    def refresh(self, now, *, fetch=request_bytes):
        value = collect(now, fetch=fetch)  # Network waits never hold the app/market lock.
        with self.lock:
            previous = self._read()
            if (previous.get("fingerprint") == value["fingerprint"]
                    and context(previous, now)["status"] == "VALIDATED_ASSESSMENT"):
                value.update({k: previous[k] for k in ("risk", "summary", "evidence_ids", "assessed_at")})
            self._write(value)
        return self.snapshot(now)

    def apply(self, evidence, assessment, now):
        if assessment is None:
            return False
        validate_assessment(assessment, evidence["articles"])
        with self.lock:
            value = self._read()
            current = context(value, now)
            if (current["fingerprint"] != evidence["fingerprint"]
                    or current["status"] in ("NEWS_FETCH_STALE", "NEWS_MISSING_OR_INVALID", "MANDATORY_NEWS_MISSING_OR_STALE")):
                return False
            value.update(assessment, assessed_at=now.isoformat())
            self._write(value)
        return True

    def run(self, stop):
        from .pc_control import collection_window
        while not stop.is_set():
            now = datetime.now(timezone.utc)
            try:
                if collection_window(now):
                    self.refresh(now)
            except Exception:
                # Atomic old evidence ages to UNKNOWN; no raw exception/provider text.
                pass
            stop.wait(120 if collection_window(now) else 5)
