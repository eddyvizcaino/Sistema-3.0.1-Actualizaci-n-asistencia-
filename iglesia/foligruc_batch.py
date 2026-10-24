"""Importación idempotente de estudiantes externos del listado trimestral."""
from .academic_members_import import source_names, key
from .db import now_iso
import re

def preview(db):
    students=db.execute('SELECT Id,FullName,MemberId FROM FStudents').fetchall()
    by_name={}
    for student in students:
        by_name.setdefault(key(student['FullName']), []).append(student)
    results=[]
    for name in source_names():
        matches=by_name.get(key(name),[])
        results.append({'name':name,'status':'nuevo' if not matches else ('existente' if len(matches)==1 else 'revisar'), 'student':matches[0] if len(matches)==1 else None})
    return results

def apply(db,user_id):
    if getattr(db,'is_postgres',False):db.execute('SELECT pg_advisory_xact_lock(274001)')
    else:db.execute('BEGIN IMMEDIATE')
    rows=preview(db)
    used={r['Code'] for r in db.execute('SELECT Code FROM FStudents').fetchall()}
    numbers=[int(m.group(1)) for c in used if (m:=re.fullmatch(r'FOL-(\d+)',c or ''))]
    n=max(numbers,default=0)
    count=0
    for item in rows:
        if item['status']!='nuevo':continue
        n+=1
        while f'FOL-{n:06d}' in used:n+=1
        code=f'FOL-{n:06d}';used.add(code)
        cur=db.execute('INSERT INTO FStudents(Code,FullName,JoinDate,Status,CreatedAt) VALUES(?,?,?,?,?)',
                       (code,item['name'],now_iso()[:10],'Activo',now_iso()))
        db.execute('INSERT INTO FHistory(StudentId,UserId,Action,Details,CreatedAt) VALUES(?,?,?,?,?)',
                   (cur.lastrowid,user_id,'Importar estudiante externo','Listado julio-septiembre; sin materias ni inscripciones',now_iso()))
        count+=1
    return count
