"""Owner's trading-email quiet hours. This never changes trading policy.

Regular notification window: 12:45 <= JST < 19:00, weekdays excluding
reviewed NSE F&O holidays. Special evening/weekend sessions do not opt in.
2026 source: https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf
Calendar checked 2026-10-08; unknown years stay quiet until reviewed.
"""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

JST=ZoneInfo('Asia/Tokyo')
HOLIDAYS_2026=frozenset(('2026-01-26','2026-03-03','2026-03-26','2026-03-31',
    '2026-04-03','2026-04-14','2026-05-01','2026-05-28','2026-06-26',
    '2026-09-14','2026-10-02','2026-10-20','2026-11-10','2026-11-24','2026-12-25'))


class MailWindowClosed(ValueError):
    pass


def regular_day(now):
    if now.tzinfo is None:return False
    local=now.astimezone(JST)
    return local.year==2026 and local.weekday()<5 and local.date().isoformat() not in HOLIDAYS_2026


def status(now):
    if now.tzinfo is None or now.astimezone(JST).year!=2026:return 'MAIL_CALENDAR_REVIEW_REQUIRED'
    if not regular_day(now) or not time(12,45)<=now.astimezone(JST).time()<time(19):
        return 'WAIT_MARKET_SESSION'
    return 'OPEN'


def require_open():
    """Last check immediately before the real provider POST, including retries."""
    value=status(datetime.now(timezone.utc))
    if value!='OPEN':raise MailWindowClosed(value)
