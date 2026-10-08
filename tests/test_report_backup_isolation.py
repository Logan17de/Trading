from datetime import datetime, timezone
from threading import Event, Thread
from types import SimpleNamespace

from nifty_engine.agent_engine import oracle_runtime as runtime
from nifty_engine.agent_engine.store import Store


class OneTick:
    def __init__(self):self.calls=0
    def wait(self,seconds):
        self.calls+=1
        return self.calls>1


def test_blocked_backup_cannot_prevent_daily_email(tmp_path,monkeypatch):
    now=datetime(2026,10,8,10,30,tzinfo=timezone.utc)
    monkeypatch.setattr(runtime,'datetime',SimpleNamespace(now=lambda tz:now))
    from nifty_engine.agent_engine import report_strategies
    monkeypatch.setattr(report_strategies,'update',lambda *args:None)
    entered=Event();release=Event();sent=[]
    def blocked_backup(*args):
        entered.set()
        assert release.wait(3)
        return {'status':'VERIFIED_BACKUP'}
    monkeypatch.setattr(runtime,'backup_database',blocked_backup)
    obj=runtime.Runtime.__new__(runtime.Runtime)
    store=Store(tmp_path/'journal.sqlite')
    obj.root=tmp_path;obj.stop=OneTick();obj.normal_catalog=None
    obj.state=SimpleNamespace(pnl_lines=SimpleNamespace(store=store),capital_ledger=SimpleNamespace(accrue=lambda now:None))
    obj.read=lambda:{'broker_writes':False}
    obj.mail=SimpleNamespace(tick=lambda view,at:sent.append(at) or {'status':'ACCEPTED'})
    worker=Thread(target=obj.backup_loop)
    worker.start()
    try:
        assert entered.wait(2)
        obj.stop=OneTick()
        obj.report_loop()
        assert sent==[now] and obj.mail_state['status']=='ACCEPTED'
        assert store.meta('oracle-backup-2026-10-08') is None
    finally:
        release.set();worker.join(3)
    assert not worker.is_alive()
    assert store.meta('oracle-backup-2026-10-08')['status']=='VERIFIED_BACKUP'
