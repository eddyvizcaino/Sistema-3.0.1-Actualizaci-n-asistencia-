"""Respaldo lógico consistente y restauración transaccional en una base vacía."""
import json
from .db import connect, now_iso
TABLES = ['Ministries','Members','Events','Users','AuditLog','LoginLog','MemberHistory','Alerts','Photos',
          'FStudents','FSubjects','FPeriods','FRecords','FHistory','MemberRegistration',
          'PublicRegistrationLinks','RateLimitLog','ImportRuns','Communities','CommunityMembers',
          'Conversions','Attendance','AttendanceVisitors','NewBelieverLeaders','ConversionFollowUps']

def export_backup(database):
    conn=connect(database)
    try:
        if getattr(conn,'is_postgres',False):
            conn.rollback()
            conn.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        else: conn.execute('BEGIN')
        tables={name:[dict(r) for r in conn.execute(f'SELECT * FROM {name} ORDER BY Id').fetchall()] for name in TABLES}
        return json.dumps({'format':'iglesia-2983','created_at':now_iso(),'tables':tables},ensure_ascii=False).encode('utf-8')
    finally:conn.close()

def restore_empty(database, content, *, replace_existing=False):
    data=json.loads(content)
    fmt=data.get('format')
    incoming=data.get('tables',{})
    if fmt not in ('iglesia-271','iglesia-2983') or not isinstance(incoming,dict):
        raise ValueError('Formato de respaldo no compatible.')
    unknown=set(incoming)-set(TABLES)
    if unknown:
        raise ValueError('El respaldo contiene tablas no compatibles: '+', '.join(sorted(unknown)))
    # Compatibilidad hacia atrás: las tablas añadidas después se inicializan vacías.
    for table in TABLES:
        incoming.setdefault(table, [])
    data['tables']=incoming
    conn=connect(database)
    try:
        if getattr(conn,'is_postgres',False):
            conn.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE')
        else:conn.execute('BEGIN IMMEDIATE')
        if not replace_existing:
            for table in TABLES:
                if table in ('Users','FSubjects'):continue
                if conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                    raise ValueError('La base destino debe estar vacía. Restaure en otra base y después cambie DATABASE_URL.')
            if conn.execute('SELECT COUNT(*) FROM Users').fetchone()[0]>1:
                raise ValueError('La base destino ya tiene usuarios.')
        # Validar todas las columnas antes de borrar el primer registro.
        for table in TABLES:
            if getattr(conn,'is_postgres',False):
                columns={r[0].lower() for r in conn.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?',(table.lower(),)).fetchall()}
            else:columns={r['name'].lower() for r in conn.execute(f'PRAGMA table_info({table})')}
            for row in data['tables'][table]:
                if not isinstance(row,dict) or not row or any(k.lower() not in columns for k in row):
                    raise ValueError('Columnas no compatibles en '+table)
        if replace_existing and not data['tables']['Users']:
            raise ValueError('El respaldo no contiene usuarios; se rechazó la restauración.')
        for table in reversed(TABLES):conn.execute(f'DELETE FROM {table}')
        for table in TABLES:
            for row in data['tables'][table]:
                names=list(row)
                # Quote validated column identifiers; PostgreSQL schema uses lowercase.
                cols=','.join('"'+k.lower()+'"' for k in names)
                conn.execute(f'INSERT INTO {table}({cols}) VALUES({",".join("?" for _ in names)})',list(row.values()))
            if getattr(conn,'is_postgres',False):
                id_exists=conn.execute('SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=? AND column_name=?',(table.lower(),'id')).fetchone()
                if id_exists:
                    seq=conn.execute("SELECT pg_get_serial_sequence(?,?)",(table.lower(),'id')).fetchone()[0]
                    if seq:
                        conn.execute(f"SELECT setval(?,COALESCE((SELECT MAX(Id) FROM {table}),1),EXISTS(SELECT 1 FROM {table}))",(seq,))
        conn.commit()
    except Exception:
        conn.rollback();raise
    finally:conn.close()
