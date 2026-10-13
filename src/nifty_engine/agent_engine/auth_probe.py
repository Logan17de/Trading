"""Scheduled GET-only Groww credential probe, never a broker-order permission."""
from __future__ import annotations

from datetime import datetime, timezone

from .alert_cadence import auth_slot
from .market_check import safe_error

KEY = 'groww-auth-probe-v1'


def inspect(now, read_profile):
    """read_profile must GET the authenticated account; never log credentials."""
    slot=auth_slot(now)
    if slot is None:
        return dict(slot=None,status='OUTSIDE_AUTH_CHECKPOINT',broker_writes=False)
    try:
        profile=read_profile()
        if not isinstance(profile,dict) or not all(
                isinstance(profile.get(k),str) and profile[k]
                for k in ('vendor_user_id','ucc')):
            raise ValueError('EXACT_AUTHENTICATED_PROFILE_REQUIRED')
        status,code='VALID',None
    except Exception as exc:
        code=safe_error(exc).get('code')
        status='INVALID' if code in ('401','403','GA005') else 'UNKNOWN'
    return dict(slot=slot,status=status,provider_code=code,
                checked_at=now.isoformat(),broker_writes=False)


def run_once(store, now, read_profile):
    slot=auth_slot(now)
    if slot is None:return None
    old=store.meta(KEY,{})
    if old.get('slot')==slot:return old
    value=inspect(now,read_profile)
    # Original provider proof timestamp; a cached validation never becomes new.
    store.set_meta(KEY,value)
    return value
