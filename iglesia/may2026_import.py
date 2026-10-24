"""Importación segura e idempotente del listado ACTUALIZACIÓN MAYO 2026."""
import csv, re, unicodedata
from pathlib import Path
from .db import connect, init_db, now_iso

def _clean(v): return ' '.join((v or '').strip().replace('´','').split())
def _fold(v): return ''.join(c for c in unicodedata.normalize('NFD', _clean(v)) if unicodedata.category(c)!='Mn').casefold()
def _phone(v): return re.sub(r'\D','',v or '')
def _ministry(v):
    k=_fold(v).upper()
    return {'DAMA':'DAMAS','DAMAS':'DAMAS','CABALLERO':'CABALLEROS','CABALLEROS':'CABALLEROS','JOVENES':'JÓVENES','ADOLESCENTE':'ADOLESCENTES','PREADOLESCENTE':'PREADOLESCENTES'}.get(k, _clean(v).upper())
def _civil(v):
    k=_fold(v).upper()
    if k in ('CASADA','CASADO','CASDA'): return 'Casado/a'
    if k in ('SOLTERA','SOLTERO'): return 'Soltero/a'
    if k in ('VIUDA','VIUDO'): return 'Viudo/a'
    if k in ('EN UNION','EN UMION','COMPR.','COMPRO.'): return 'Unión Libre'
    return ''
def _bapt(v):
    k=_fold(v).upper(); return 'Sí' if k=='SI' else ('No' if k=='NO' else '')
def _status(v):
    k=_fold(v).upper()
    if not k: return ''
    if 'FUERA DE LA ZONA' in k: return 'Fuera de la zona'
    if 'TRASL' in k or 'OTRA IGLESIA' in k: return 'Trasladado'
    if 'NO ESTA ASISTIENDO' in k: return 'Inactivo'
    if k.startswith('DESC') or 'DESCARRIAD' in k: return 'Descarriado'
    if k.startswith('ACT'): return 'Activo'
    return ''
def _split(full):
    p=full.split()
    if len(p)<2: return full,''
    cut=1 if len(p)<=3 else 2
    return ' '.join(p[:cut]),' '.join(p[cut:])
def import_may_2026(database, dry_run=False):
    init_db(database); db=connect(database); path=Path(__file__).resolve().parent.parent/'data'/'actualizacion_mayo_2026.csv'
    stats={'source':0,'inserted':0,'duplicates':0,'ministries_created':0,'already_applied':False}
    try:
        if getattr(db,'is_postgres',False):
            db.execute('SELECT pg_advisory_xact_lock(292026)')
        marker = db.execute('SELECT ImportKey FROM ImportRuns WHERE ImportKey=?', ('ACTUALIZACION_MAYO_2026',)).fetchone()
        if marker:
            stats['already_applied'] = True
            return stats
        existing=db.execute('SELECT Id,FullName,Phone FROM Members WHERE DeletedAt IS NULL').fetchall(); keys=set()
        for x in existing:
            n=_fold(x['FullName']); ph=_phone(x['Phone']); keys.add((n,ph)); keys.add((n,''))
        max_code=0
        for x in db.execute('SELECT MemberCode FROM Members WHERE MemberCode IS NOT NULL').fetchall():
            m=re.fullmatch(r'MEM-(\d+)',x['MemberCode'] or '')
            if m: max_code=max(max_code,int(m.group(1)))
        with path.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                stats['source']+=1; full=_clean(row['NombreCompleto']); phone=_clean(row['Telefono']); ph=_phone(phone); nk=_fold(full)
                if (nk,ph) in keys or (nk,'') in keys: stats['duplicates']+=1; continue
                mn=_ministry(row['Ministerio']); mid=None
                if mn:
                    mr=db.execute('SELECT Id FROM Ministries WHERE upper(Name)=upper(?)',(mn,)).fetchone()
                    if not mr:
                        cur=db.execute('INSERT INTO Ministries(Name) VALUES(?)',(mn.title(),)); mid=cur.lastrowid; stats['ministries_created']+=1
                    else: mid=mr['Id']
                first,last=_split(full); max_code+=1
                db.execute('INSERT INTO Members(FullName,FirstName,LastName,Phone,MinistryId,MaritalStatus,Baptized,Status,MemberCode,CreatedAt,GroupName,ChurchRole) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (full,first,last,phone or None,mid,_civil(row['EstadoCivil']),_bapt(row['Bautizado']),_status(row['EstadoMiembro']),f'MEM-{max_code:03d}',now_iso(),'','Miembro'))
                stats['inserted']+=1; keys.add((nk,ph)); keys.add((nk,''))
        if dry_run:
            db.rollback()
        else:
            db.execute('INSERT INTO ImportRuns(ImportKey,CreatedAt,Details) VALUES(?,?,?) RETURNING ImportKey',
                       ('ACTUALIZACION_MAYO_2026', now_iso(),
                        f"Fuente={stats['source']}; agregados={stats['inserted']}; duplicados={stats['duplicates']}"))
            db.commit()
        return stats
    except Exception:
        db.rollback(); raise
    finally: db.close()
