import json
from datetime import timedelta
from email.utils import format_datetime

import pytest

from nifty_engine.agent_engine.pc_news import FEEDS, PcNews, collect, context
from nifty_engine.agent_engine.pc_control import PcJournal, PcMonitor, validate_analysis
from test_pc_control import NOW, SYMBOL, create_root, owner_protocol, result_for, snapshot


def feed(url, now=NOW):
    host = 'rbi.org.in' if url == FEEDS[0] else 'economictimes.indiatimes.com'
    return (f'<rss><channel><item><title>Market evidence</title><link>https://{host}/test</link>'
            f'<pubDate>{format_datetime(now)}</pubDate></item></channel></rss>').encode()


def test_partial_news_preserves_evidence_but_never_low():
    def fetch(url):
        if url == FEEDS[0]:
            raise OSError('PRIVATE provider failure')
        return feed(url)
    raw = collect(NOW, fetch=fetch)
    news = context(raw, NOW)
    assert len(news['articles']) == 1 and news['risk'] == 'UNKNOWN'
    assert news['status'] == 'MANDATORY_NEWS_MISSING_OR_STALE'
    assert 'PRIVATE' not in json.dumps(raw)
    assert context(raw, NOW + timedelta(seconds=181))['status'] == 'NEWS_FETCH_STALE'


def test_assessment_binds_exact_evidence_and_expires_without_network_refresh(tmp_path):
    worker = PcNews(tmp_path)
    evidence = worker.refresh(NOW, fetch=feed)
    assessment = dict(risk='LOW', summary='Supplied sources reviewed, not a forecast',
                      evidence_ids=[a['id'] for a in evidence['articles']])
    assert worker.apply(evidence, assessment, NOW)
    assert worker.snapshot(NOW)['risk'] == 'LOW'
    worker.refresh(NOW + timedelta(seconds=120), fetch=feed)
    assert worker.snapshot(NOW + timedelta(seconds=120))['risk'] == 'LOW'
    assert worker.snapshot(NOW + timedelta(seconds=181))['risk'] == 'UNKNOWN'
    changed = lambda url: feed(url, NOW + timedelta(minutes=1))
    worker.refresh(NOW + timedelta(seconds=120), fetch=changed)
    assert not worker.apply(evidence, assessment, NOW + timedelta(seconds=120))
    assert worker.snapshot(NOW + timedelta(seconds=120))['risk'] == 'UNKNOWN'


def test_redirect_or_external_article_not_enabled_and_corrupt_file_is_unknown(tmp_path):
    raw = collect(NOW, fetch=lambda url: feed(url).replace(b'/test', b'/test').replace(
        b'https://rbi.org.in', b'https://unapproved.example'))
    assert context(raw, NOW)['status'] == 'MANDATORY_NEWS_MISSING_OR_STALE'
    worker = PcNews(tmp_path)
    worker.path.parent.mkdir(); worker.path.write_text('{bad')
    assert worker.snapshot(NOW)['risk'] == 'UNKNOWN'


def test_structured_news_validation_rejects_invented_ids_or_missing_source(tmp_path):
    monitor = PcMonitor(create_root(tmp_path)); monitor.news.refresh(NOW, fetch=feed)
    monitor.tick(snapshot(), owner_protocol(), NOW)
    request = monitor.journal.claim(NOW); result = result_for(request)
    result['news_assessment'] = dict(risk='LOW', summary='Reviewed evidence', evidence_ids=['invented'])
    with pytest.raises(ValueError):
        validate_analysis(result, request, NOW)
    result['news_assessment']['evidence_ids'] = [request['news']['articles'][0]['id']]
    with pytest.raises(ValueError):
        validate_analysis(result, request, NOW)
    result['news_assessment']['evidence_ids'] = [a['id'] for a in request['news']['articles']]
    validate_analysis(result, request, NOW)


def test_periodic_news_and_barrier_priorities_keep_daily_budget_and_restart_dedup(tmp_path):
    monitor = PcMonitor(create_root(tmp_path)); monitor.news.refresh(NOW, fetch=feed)
    monitor.tick(snapshot(), owner_protocol(), NOW)
    monitor.analyze_once(result_for, NOW, clock=lambda:NOW)
    at = NOW.replace(hour=15, minute=0); raw = snapshot(); raw['finished_at'] = at.isoformat()
    for probe in raw['probes'].values(): probe['received_at'] = at.isoformat()
    monitor.tick(raw, owner_protocol(), at)
    assert len(monitor.journal.store.read("SELECT * FROM pc_requests WHERE id LIKE 'levels-%'")) == 4
    monitor = PcMonitor(tmp_path); monitor.tick(raw, owner_protocol(), at)
    assert len(monitor.journal.store.read("SELECT * FROM pc_requests WHERE id LIKE 'levels-%'")) == 4
    monitor.journal.enqueue('touch-priority', dict(index='NIFTY'), at)
    assert monitor.journal.claim(at)['request_id'] == 'touch-priority'
    for n in range(8):
        monitor.journal.enqueue(f'extra-{n}', {}, at)
        monitor.journal.claim(at)
    assert monitor.journal.claim(at) is None
    assert monitor.tick(raw, owner_protocol(), at)['analysis_budget']['status'] == 'DAILY_CAP_REACHED'


def test_slot_owner_survives_overnight_and_cannot_be_relabelled_as_swing(tmp_path):
    path = tmp_path/'journal.sqlite3'; journal = PcJournal(path)
    journal.reserve('one-slot', SYMBOL, 'SELL', 65, strategy='EVERYDAY')
    journal.reserve('one-slot', 'NIFTY26O0824000CE', 'BUY', 65, strategy='EVERYDAY')
    journal = PcJournal(path)
    assert journal.slot_status()['strategy'] == 'EVERYDAY'
    assert journal.slot_status()['new_entry_blocked']
    with pytest.raises(ValueError):
        journal.reserve('one-slot', SYMBOL, 'SELL', 65, strategy='SWING')
    with pytest.raises(ValueError):
        journal.reserve('other-slot', SYMBOL, 'SELL', 65, strategy='SWING')
    assert journal.slot_status()['swing'] == 'RESEARCH_ONLY_1845_OUTSIDE_ACTION_WINDOW'
