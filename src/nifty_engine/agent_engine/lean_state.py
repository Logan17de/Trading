"""Explicit offline operational copy; source/history is retained unchanged.

The deployer must stop the paused/Off observer and hold the owner setup lock
before replacing its journal. This function creates a NEW path only.
"""
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

KEEP = {'meta','reports','pc_orders','pc_protected','pc_slots','pc_trade_audit'}


def operational_copy(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or source==destination: raise ValueError('NEW_DESTINATION_REQUIRED')
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    def quote(name): return '"'+name.replace('"','""')+'"'
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
            src.execute('BEGIN')
            schema=src.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
            for kind,name,sql in schema:
                if kind=='table': dst.execute(sql)
            counts={}
            for table in sorted(KEEP):
                if not any(kind=='table' and name==table for kind,name,_ in schema): continue
                cursor=src.execute('SELECT * FROM '+quote(table))
                insert='INSERT INTO '+quote(table)+' VALUES('+','.join('?' for _ in cursor.description)+')'
                counts[table]=0
                while True:
                    rows=cursor.fetchmany(128)
                    if not rows: break
                    dst.executemany(insert,rows);counts[table]+=len(rows)
            for kind,name,sql in schema:
                if kind!='table': dst.execute(sql)
            version=src.execute('PRAGMA user_version').fetchone()[0]
            dst.execute('PRAGMA user_version='+str(int(version)));dst.commit()
            if dst.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]: raise ValueError('OPERATIONAL_COPY_INTEGRITY_FAILED')
            for table,count in counts.items():
                if dst.execute('SELECT COUNT(*) FROM '+quote(table)).fetchone()[0]!=count:
                    raise ValueError('OPERATIONAL_COPY_ROW_MISMATCH')
            src.rollback()
        digest=hashlib.sha256()
        with destination.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        return dict(status='VERIFIED_OPERATIONAL_COPY',counts=counts,bytes=destination.stat().st_size,sha256=digest.hexdigest(),source_retained=True)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
