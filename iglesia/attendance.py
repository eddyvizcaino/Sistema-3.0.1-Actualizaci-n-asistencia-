"""Registro simple de asistencia por culto."""
import datetime
import csv
import io
from flask import Response
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from .db import audit, get_db, now_iso, dr_now
from .security import login_required, clean, is_admin

bp=Blueprint('attendance',__name__,url_prefix='/attendance')
SERVICES={
 'friday':('Viernes · Culto','19:30','22:00'),
 'sunday_am':('Domingo · Mañana','10:00','12:00'),
 'sunday_pm':('Domingo · Tarde/Noche','18:30','21:30'),
 'special':('Culto Especial','00:00','23:59'),
 'vigil':('Vigilia','00:00','23:59'),
 'communion':('Santa Cena','00:00','23:59'),
 'conference':('Conferencia','00:00','23:59'),
}

def _allowed(): return g.user and g.user['Role'] in ('Admin','Pastor','AssistantPastor','Attendance')
def _service_for(dt):
    mins=dt.hour*60+dt.minute
    if dt.weekday()==4 and 19*60+30 <= mins <= 22*60: return 'friday'
    if dt.weekday()==6 and 10*60 <= mins <= 12*60: return 'sunday_am'
    if dt.weekday()==6 and 18*60+30 <= mins <= 21*60+30: return 'sunday_pm'
    return None

def _service_date(key, value=None):
    d=value or dr_now().date().isoformat()
    return d

@bp.before_request
@login_required
def gate():
    if not _allowed(): abort(403)

@bp.route('/',methods=['GET'])
def index():
    # El rol Registro de Asistencia siempre usa la PWA móvil.
    if g.user and g.user['Role'] == 'Attendance':
      return redirect(url_for('attendance.mobile_app', **request.args))
    db=get_db(); now=dr_now(); auto=_service_for(now)
    requested=request.args.get('service')
    key=(requested if is_admin() and requested in SERVICES else auto)
    if key not in SERVICES: key=None
    day=request.args.get('date') or now.date().isoformat()
    ministries=db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()
    rows=db.execute("""SELECT m.Id,m.MemberCode,m.FirstName,m.LastName,m.FullName,m.Phone,m.Status,mi.Name Ministry
      FROM Members m LEFT JOIN Ministries mi ON mi.Id=m.MinistryId
      WHERE m.DeletedAt IS NULL AND lower(coalesce(m.Status,'Activo'))='activo'
      ORDER BY coalesce(mi.Name,''),coalesce(m.FirstName,m.FullName),coalesce(m.LastName,'')""").fetchall()
    present=[]; visitors=[]
    if key:
      present=db.execute("""SELECT a.Id,a.MemberId,a.RegisteredAt,m.MemberCode,m.FirstName,m.LastName,m.FullName,mi.Name Ministry
        FROM Attendance a JOIN Members m ON m.Id=a.MemberId LEFT JOIN Ministries mi ON mi.Id=m.MinistryId
        WHERE a.ServiceDate=? AND a.ServiceKey=? ORDER BY a.RegisteredAt DESC""",(day,key)).fetchall()
      visitors=db.execute("SELECT * FROM AttendanceVisitors WHERE ServiceDate=? AND ServiceKey=? ORDER BY RegisteredAt DESC",(day,key)).fetchall()
    return render_template('attendance.html',title='ASISTENCIA',rows=rows,ministries=ministries,present=present,visitors=visitors,
      present_ids={r['MemberId'] for r in present},service_key=key,service=SERVICES.get(key),service_date=day,auto_service=auto,services=SERVICES,is_admin_user=is_admin())

@bp.route('/present',methods=['POST'])
def present():
    key=request.form.get('service_key'); day=request.form.get('service_date'); mid=request.form.get('member_id',type=int)
    if key not in SERVICES or not day or not mid: abort(400)
    _validate_service_write(key,day)
    db=get_db(); member=db.execute('SELECT Id FROM Members WHERE Id=? AND DeletedAt IS NULL',(mid,)).fetchone()
    if not member: abort(404)
    if not db.execute('SELECT Id FROM Attendance WHERE MemberId=? AND ServiceDate=? AND ServiceKey=?',(mid,day,key)).fetchone():
      cur=db.execute('INSERT INTO Attendance(MemberId,ServiceDate,ServiceKey,RegisteredAt,RegisteredBy) VALUES(?,?,?,?,?)',(mid,day,key,now_iso(),g.user['Id']))
      audit('Registrar asistencia','Asistencia',cur.lastrowid,f'{key} {day}; miembro={mid}'); db.commit()
    target = 'attendance.mobile_app' if request.form.get('app') == '1' or (g.user and g.user['Role'] == 'Attendance') else 'attendance.index'
    return redirect(url_for(target,service=key,date=day))

@bp.route('/present/<int:aid>/remove',methods=['POST'])
def remove_present(aid):
    db=get_db(); row=db.execute('SELECT * FROM Attendance WHERE Id=?',(aid,)).fetchone()
    if not row: abort(404)
    # Administrador puede corregir históricos; los demás solo durante el mismo culto.
    if not is_admin() and (row['ServiceDate'] != dr_now().date().isoformat() or _service_for(dr_now()) != row['ServiceKey']):
      flash('La asistencia solo puede corregirse durante el mismo culto.'); return redirect(url_for('attendance.index',service=row['ServiceKey'],date=row['ServiceDate']))
    db.execute('DELETE FROM Attendance WHERE Id=?',(aid,)); audit('Corregir asistencia','Asistencia',aid,'Marcación eliminada durante el mismo culto'); db.commit()
    return redirect(url_for('attendance.mobile_app' if request.form.get('app') == '1' or g.user['Role']=='Attendance' else 'attendance.index',service=row['ServiceKey'],date=row['ServiceDate']))

@bp.route('/visitor',methods=['POST'])
def visitor():
    key=request.form.get('service_key'); day=request.form.get('service_date'); name=clean(request.form.get('name'),160); phone=clean(request.form.get('phone'),40)
    if key not in SERVICES or not day or not name: abort(400)
    _validate_service_write(key,day)
    db=get_db(); cur=db.execute('INSERT INTO AttendanceVisitors(FullName,Phone,ServiceDate,ServiceKey,RegisteredAt,RegisteredBy) VALUES(?,?,?,?,?,?)',(name,phone or None,day,key,now_iso(),g.user['Id']))
    audit('Registrar visitante','Asistencia',cur.lastrowid,f'{key} {day}'); db.commit()
    target = 'attendance.mobile_app' if request.form.get('app') == '1' or (g.user and g.user['Role'] == 'Attendance') else 'attendance.index'
    return redirect(url_for(target,service=key,date=day))

@bp.route('/app', methods=['GET'])
def mobile_app():
    """PWA móvil de registro; usa la misma BD y endpoints de asistencia."""
    db=get_db(); now=dr_now(); auto=_service_for(now)
    requested=request.args.get('service')
    key=(requested if is_admin() and requested in SERVICES else auto)
    if key not in SERVICES: key=None
    day=request.args.get('date') or now.date().isoformat()
    ministries=db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()
    rows=db.execute("""SELECT m.Id,m.MemberCode,m.FirstName,m.LastName,m.FullName,m.Phone,m.Status,mi.Name Ministry
      FROM Members m LEFT JOIN Ministries mi ON mi.Id=m.MinistryId
      WHERE m.DeletedAt IS NULL AND lower(coalesce(m.Status,'Activo'))='activo'
      ORDER BY coalesce(m.FirstName,m.FullName),coalesce(m.LastName,'')""").fetchall()
    present=[]; visitors=[]
    if key:
      present=db.execute("""SELECT a.Id,a.MemberId,a.RegisteredAt,m.MemberCode,m.FirstName,m.LastName,m.FullName,mi.Name Ministry
        FROM Attendance a JOIN Members m ON m.Id=a.MemberId LEFT JOIN Ministries mi ON mi.Id=m.MinistryId
        WHERE a.ServiceDate=? AND a.ServiceKey=? ORDER BY a.RegisteredAt DESC""",(day,key)).fetchall()
      visitors=db.execute("SELECT * FROM AttendanceVisitors WHERE ServiceDate=? AND ServiceKey=? ORDER BY RegisteredAt DESC",(day,key)).fetchall()
    return render_template('attendance_app.html', rows=rows, ministries=ministries, present=present, visitors=visitors,
      present_ids={r['MemberId'] for r in present}, service_key=key, service=SERVICES.get(key), service_date=day,
      auto_service=auto, services=SERVICES, is_admin_user=is_admin())

def _validate_service_write(key, day):
    try:
        actual = datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        abort(400)
    if not is_admin() and (actual != dr_now().date() or key != _service_for(dr_now())):
        abort(403)


def _attendance_report():
    db=get_db()
    members=db.execute("""SELECT m.Id,m.MemberCode,m.FirstName,m.LastName,m.FullName,m.Phone,mi.Name Ministry
       FROM Members m LEFT JOIN Ministries mi ON mi.Id=m.MinistryId
       WHERE m.DeletedAt IS NULL AND lower(coalesce(m.Status,'Activo'))='activo'
       ORDER BY coalesce(mi.Name,''),m.FullName""").fetchall()
    attend=db.execute('SELECT MemberId,ServiceDate,ServiceKey FROM Attendance ORDER BY ServiceDate DESC').fetchall()
    sessions=db.execute('SELECT ServiceDate,ServiceKey FROM Attendance UNION SELECT ServiceDate,ServiceKey FROM AttendanceVisitors ORDER BY ServiceDate DESC').fetchall()
    dates={ (r['ServiceDate'],r['ServiceKey']) for r in sessions }
    # A service is known to have happened only if it has attendance or visitors.
    fridays=sorted({d for d,k in dates if k=='friday' and datetime.date.fromisoformat(d).weekday()==4},reverse=True)
    attended={ (r['MemberId'],r['ServiceDate'],r['ServiceKey']) for r in attend }
    last={}; counts={}
    for r in attend:
        mid=r['MemberId']; counts[mid]=counts.get(mid,0)+1
        if mid not in last or r['ServiceDate']>last[mid]: last[mid]=r['ServiceDate']
    result=[]
    for m in members:
        mid=m['Id']; missed=0
        for d in fridays:
            if (mid,d,'friday') in attended: break
            missed+=1
        attended_sessions=sum((mid,d,k) in attended for d,k in dates)
        total=len(dates)
        result.append(dict(code=m['MemberCode'] or '',name=((' '.join(filter(None,[m['FirstName'],m['LastName']]))).strip() or m['FullName']),phone=m['Phone'] or '',ministry=m['Ministry'] or 'Sin ministerio',last=last.get(mid,''),present=attended_sessions,absent=total-attended_sessions,percentage=round(attended_sessions*100/total) if total else None,missed_fridays=missed,alert=(len(fridays)>=2 and missed>=2)))
    return result, len(dates), len(fridays)

@bp.route('/seguimiento')
def follow_up():
    if not is_admin() and g.user['Role'] not in ('Pastor','AssistantPastor'): abort(403)
    data,total,fridays=_attendance_report()
    ministry=request.args.get('ministry','').strip()
    only=request.args.get('only','')=='alerts'
    if ministry: data=[r for r in data if r['ministry']==ministry]
    if only: data=[r for r in data if r['alert']]
    return render_template('attendance_followup.html',data=data,total=total,fridays=fridays,only=only,ministry=ministry,ministries=sorted({r['ministry'] for r in _attendance_report()[0]}))

@bp.route('/seguimiento.csv')
def follow_up_csv():
    if not is_admin() and g.user['Role'] not in ('Pastor','AssistantPastor'): abort(403)
    data,_,_=_attendance_report(); f=io.StringIO(); w=csv.writer(f)
    w.writerow(['MEM','Nombre','Teléfono','Ministerio','Última asistencia','Presentes','Ausencias','Porcentaje','Viernes consecutivos ausente','Alerta'])
    for r in data: w.writerow([r['code'],r['name'],r['phone'],r['ministry'],r['last'],r['present'],r['absent'],r['percentage'],r['missed_fridays'],'Sí' if r['alert'] else 'No'])
    return Response('\ufeff'+f.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=seguimiento_asistencia.csv'})
