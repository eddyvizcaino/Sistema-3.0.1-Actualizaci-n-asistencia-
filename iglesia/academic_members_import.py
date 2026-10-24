"""Importación del listado julio-septiembre SOLO al módulo Miembros."""
import csv, re, unicodedata
from pathlib import Path
from .db import connect, init_db, now_iso
from .may2026_import import _split

IMPORT_KEY = 'TRIMESTRE_JUL_SEP_2026_SOLO_MIEMBROS'
SOURCE = Path(__file__).resolve().parent.parent / 'data' / 'foligruc_julio_septiembre_nombres.csv'

def key(name):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD', str(name or '')) if unicodedata.category(c) != 'Mn').casefold().split())

def source_names():
    with SOURCE.open(encoding='utf-8-sig', newline='') as f:
        return list(dict.fromkeys(' '.join(r['NombreCompleto'].split()) for r in csv.DictReader(f) if r.get('NombreCompleto','').strip()))

def preview(db):
    existing = {key(r['FullName']) for r in db.execute('SELECT FullName FROM Members').fetchall() if r['FullName']}
    # Coincidencias parciales no se crean automáticamente: necesitan revisión humana.
    entries=[]
    for name in source_names():
        k=key(name)
        if k in existing: status='existente'
        elif len(name.split()) < 2: status='revisar'
        else: status='nuevo'
        entries.append({'name':name, 'status':status})
    return entries

def import_academic_names(database, dry_run=True):
    init_db(database)
    db=connect(database)
    try:
        if getattr(db,'is_postgres',False): db.execute('SELECT pg_advisory_xact_lock(292027)')
        else: db.execute('BEGIN IMMEDIATE')
        applied=bool(db.execute('SELECT ImportKey FROM ImportRuns WHERE ImportKey=?',(IMPORT_KEY,)).fetchone())
        entries=preview(db)
        stats={'source':len(entries),'inserted':0,'duplicates':sum(x['status']=='existente' for x in entries), 'ambiguous':sum(x['status']=='revisar' for x in entries),'already_applied':applied}
        if applied or dry_run:
            db.rollback(); return stats
        codes=[int(m.group(1)) for r in db.execute('SELECT MemberCode FROM Members WHERE MemberCode IS NOT NULL').fetchall() if (m:=re.fullmatch(r'MEM-(\d+)',r['MemberCode'] or ''))]
        next_code=max(codes,default=0)
        for entry in entries:
            if entry['status']!='nuevo': continue
            name=entry['name']; first,last=_split(name); next_code+=1
            db.execute('INSERT INTO Members(FullName,FirstName,LastName,Status,MemberCode,CreatedAt,GroupName,ChurchRole) VALUES(?,?,?,?,?,?,?,?)', (name,first,last,'Activo',f'MEM-{next_code:06d}',now_iso(),'','Miembro'))
            stats['inserted']+=1
        db.execute('INSERT INTO ImportRuns(ImportKey,CreatedAt,Details) VALUES(?,?,?)',(IMPORT_KEY,now_iso(),str(stats)))
        db.commit(); return stats
    except Exception:
        db.rollback(); raise
    finally: db.close()
