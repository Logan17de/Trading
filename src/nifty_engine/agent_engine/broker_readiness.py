"""GET-only provider diagnostics. Empty lists never prove protective execution."""
from __future__ import annotations

from datetime import timedelta
from .contracts import IST
from .execution import fresh

KEY='report-broker-readiness'


def inspect_gtt(read,now):
    result=dict(at=now.isoformat(),broker_writes=False,order_write_verified=False,
        persistent_owned_gtt_verified=False,generated_child_verified=False,
        reason='OWNER_CONTROLLED_PROVIDER_VALIDATION_REQUIRED',list_scope='EXPLICIT_28_DAYS_IST',lists={})
    end=now.astimezone(IST)
    window=dict(start_date_time=(end-timedelta(days=28)).replace(tzinfo=None).isoformat(timespec='seconds'),
                end_date_time=end.replace(tzinfo=None).isoformat(timespec='seconds'))
    result['window']=window
    for status in ('ACTIVE','COMPLETED','CANCELLED'):
        try:
            count=0
            for page in range(4):
                body=read(segment='FNO',smart_order_type='GTT',status=status,page=page,page_size=50,timeout=5,**window)
                rows=body['orders']
                if not isinstance(rows,list) or len(rows)>50 or any(not isinstance(r,dict) for r in rows):
                    raise ValueError('EXACT_GTT_LIST_REQUIRED')
                count+=len(rows)
                if len(rows)<50:break
            else:raise ValueError('GTT_LIST_TRUNCATED')
            result['lists'][status]=dict(status='AVAILABLE',count=count,complete=True)
        except Exception:result['lists'][status]=dict(status='UNKNOWN',count=None,complete=False)
    result['gtt_read_api']='AVAILABLE' if all(r['complete'] for r in result['lists'].values()) else 'UNKNOWN'
    if result['gtt_read_api']=='AVAILABLE' and not sum(r['count'] for r in result['lists'].values()):
        result['reason']='NO_RETURNED_GTT_OR_CHILD_EVIDENCE'
    return result


def summary(store,now):
    value=store.meta(KEY,{})
    try:current=fresh(value['at'],now,3600)
    except (KeyError,TypeError,ValueError):current=False
    return dict(checked_at=value.get('at'),read_api=value.get('gtt_read_api','UNKNOWN') if current else 'UNKNOWN_OR_STALE',
        list_scope=value.get('list_scope','UNSPECIFIED'),window=value.get('window'),
        counts={k:r.get('count') if current else None for k,r in value.get('lists',{}).items()},
        persistent_owned_gtt_verified=False,generated_child_verified=False,
        reason=value.get('reason','PROVIDER_AUDIT_NOT_RUN'),broker_writes=False)
