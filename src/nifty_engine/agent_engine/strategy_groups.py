"""Two private owner choices; research selection never grants broker writes."""
from .contracts import dumps

IDS=('normal_theta','research')
EXECUTABLE=('normal_theta',)
KEY='strategy-groups-v1'


def validate_command(body):
    if (not isinstance(body,dict) or set(body)!={'id','enabled','revision'}
            or body['id'] not in IDS or type(body['enabled']) is not bool
            or type(body['revision']) is not int or not 0<=body['revision']<2**31):
        raise ValueError('INVALID_STRATEGY_SWITCH')


def read(store):
    value=store.meta(KEY,{})
    if not value:return dict(format=KEY,revision=0,enabled={i:False for i in IDS},updated_at=None)
    if (value.get('format')!=KEY or set(value.get('enabled',{}))!=set(IDS)
            or any(type(v) is not bool for v in value['enabled'].values())
            or type(value.get('revision')) is not int or value['revision']<0):
        raise ValueError('STRATEGY_CONTROLS_CORRUPT')
    return value


def set_switch(store,body,now):
    validate_command(body)
    with store.transaction() as db:
        value=read(store)
        if value['revision']!=body['revision']:return dict(status='REVISION_CONFLICT',controls=value,broker_writes=False)
        if value['enabled'][body['id']]==body['enabled']:return dict(status='UNCHANGED',controls=value,broker_writes=False)
        value['enabled'][body['id']]=body['enabled'];value.update(revision=value['revision']+1,updated_at=now.isoformat())
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(KEY,dumps(value)))
    return dict(status='SAVED',controls=value,broker_writes=False)
