"""Importación de V2.5/V2.6 y del FOLIGRUC anterior, sin modificar el origen."""
import sqlite3
import tempfile
from pathlib import Path
from .db import init_db, connect, now_iso, import_legacy_photos
from .portable_backup import TABLES, export_backup


def convert_sqlite(source, photos=None):
    source=Path(source).resolve()
    if not source.is_file():raise ValueError('No existe el archivo SQLite de origen.')
    original=sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True);original.row_factory=sqlite3.Row
    try:
        names={r[0] for r in original.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'Members','Users','Ministries'}.issubset(names):raise ValueError('No parece un respaldo de Iglesia Web.')
        # Fresh destination supplies column defaults and excludes removed modules.
        with tempfile.TemporaryDirectory() as folder:
            local=str(Path(folder)/'migration.db');init_db(local);out=connect(local)
            def rows(table):return [dict(r) for r in original.execute('SELECT * FROM '+table)] if table in names else []
            def insert(table,row):
                columns={r['name'] for r in out.execute('PRAGMA table_info('+table+')')}
                data={k:v for k,v in row.items() if k in columns}
                out.execute('INSERT INTO '+table+'('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')',list(data.values()))
            out.execute('DELETE FROM Users')
            for table in TABLES[:9]:
                for row in rows(table):
                    if table=='Events':
                        if row.get('Description'):row['Notes']='\n'.join(x for x in (row.get('Notes'),row['Description']) if x)
                        if row.get('ReminderDate') and not row.get('Reminder'):row['Reminder']=row['ReminderDate']
                    if table=='Users':
                        if row.get('Role')=='Secretary':row['Role']='AssistantPastor'
                        row.setdefault('Permissions','home,members,ministries,foligruc,birthdays,events')
                    insert(table,row)
            if out.execute('SELECT COUNT(*) FROM Users').fetchone()[0]==0:raise ValueError('El respaldo no contiene usuarios.')
            subjects=rows('FSubjects');mapping={}
            for sub in subjects:
                position=sub.get('Position',sub['Id'])
                if position not in range(1,16):raise ValueError('El pénsum anterior contiene materias fuera de las 15 oficiales; requiere revisar su equivalencia.')
                mapping[sub['Id']]=position
                teacher=sub.get('Teacher',sub.get('TeacherName',''))
                out.execute('UPDATE FSubjects SET Teacher=? WHERE Id=?',(teacher,position))
            for row in rows('FPeriods'):
                row.setdefault('Closed',int(row.get('Status') in ('Cerrado','Cerrada')));insert('FPeriods',row)
            for row in rows('FStudents'):
                row.setdefault('FullName',row.get('Name',''));row.setdefault('JoinDate',row.get('EntryDate'));row.setdefault('CreatedAt',now_iso())
                if 'MinistryId' not in row:
                    member=out.execute('SELECT MinistryId FROM Members WHERE Id=?',(row.get('MemberId'),)).fetchone()
                    row['MinistryId']=member['MinistryId'] if member else None
                insert('FStudents',row)
            def subject(sid):
                if sid not in mapping:raise ValueError('Hay un historial que referencia una materia sin equivalencia.')
                return mapping[sid]
            if 'FRecords' in names:
                for row in rows('FRecords'):row['SubjectId']=subject(row['SubjectId']);insert('FRecords',row)
                for row in rows('FHistory'):insert('FHistory',row)
            else:
                teachers={r['Id']:r['Name'] for r in rows('FTeachers')}
                for row in rows('FEnrollments'):
                    status={'Aprobado':'Aprobada','Reprobado':'Reprobada'}.get(row['Status'],row['Status'])
                    insert('FRecords',{'StudentId':row['StudentId'],'SubjectId':subject(row['SubjectId']),'PeriodId':row['PeriodId'],
                        'Status':status,'Grade':None,'ApprovedDate':(row.get('FinishedAt') or '')[:10] or None,
                        'Teacher':row.get('TeacherName') or teachers.get(row.get('TeacherId'),''),'Notes':row.get('Notes'),
                        'CreatedAt':row.get('CreatedAt') or now_iso(),'Previous':0})
                for row in rows('FPriorCredits'):
                    insert('FRecords',{'StudentId':row['StudentId'],'SubjectId':subject(row['SubjectId']),'Status':'Aprobada',
                        'Grade':None,'ApprovedDate':row.get('ApprovedOn'),'Previous':1,
                        'Teacher':row.get('Teacher'),'Notes':'\n'.join(x for x in (row.get('PeriodLabel'),row.get('Notes')) if x),
                        'CreatedAt':row.get('CreatedAt') or now_iso(),'CreatedBy':row.get('CreatedBy')})
            for row in out.execute('SELECT Id FROM FStudents').fetchall():
                insert('FHistory',{'StudentId':row['Id'],'Action':'Importar','Details':'Historial académico conservado desde SQLite anterior','CreatedAt':now_iso()})
            out.commit();out.close()
            if photos:import_legacy_photos(local,photos)
            return export_backup(local)
    finally:original.close()
