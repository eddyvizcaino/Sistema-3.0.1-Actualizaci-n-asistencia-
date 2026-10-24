"""Reportes de miembros (Excel/PDF, según permisos) y reporte completo (solo administrador)."""
import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from flask import Blueprint, render_template, send_file, request

from .db import audit, get_db
from .security import NAME_SQL, admin_required, pastoral_required, excel_safe, login_required, scope, where

bp = Blueprint('reports', __name__)

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _sheet(ws, headers, rows):
    from openpyxl.styles import Font
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = 'A2'
    for r in rows:
        ws.append([excel_safe(v) for v in (r.values() if isinstance(r, dict) else r)])
    for col in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)


def filtered_members():
    db=get_db();sc,sp=scope('m')
    rows=db.execute('SELECT m.*,mi.Name AS MinistryName FROM Members m LEFT JOIN Ministries mi ON mi.Id=m.MinistryId'+where(sc)+' ORDER BY m.FullName',sp).fetchall()
    result=[];today=datetime.date.today()
    for r in rows:
        age=None
        try:
            birth=datetime.date.fromisoformat(r['BirthDate']);age=today.year-birth.year-((today.month,today.day)<(birth.month,birth.day))
        except (ValueError,TypeError):pass
        if request.args.get('ministry',type=int) and r['MinistryId']!=request.args.get('ministry',type=int):continue
        if request.args.get('status') and r['Status']!=request.args['status']:continue
        if request.args.get('baptized')=='1' and not r['BaptismDate']:continue
        if request.args.get('baptized')=='0' and r['BaptismDate']:continue
        if request.args.get('from') and (not r['JoinDate'] or r['JoinDate']<request.args['from']):continue
        if request.args.get('to') and (not r['JoinDate'] or r['JoinDate']>request.args['to']):continue
        low=request.args.get('min_age',type=int);high=request.args.get('max_age',type=int)
        if low is not None and (age is None or age<low):continue
        if high is not None and (age is None or age>high):continue
        result.append([r['FullName'],r['Cedula'],r['Phone'],r['MinistryName'],r['Status'],r['JoinDate'],r['BaptismDate'],age,r['ChurchRole']])
    return result

HEADERS=['Nombre','Cédula','Teléfono','Ministerio','Estado','Ingreso','Bautizo','Edad','Cargo']

@bp.route('/reports')
@login_required
def reports():
    return render_template('reports.html', title='REPORTES',ministries=get_db().execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall())

@bp.route('/reports/members.xlsx')
@login_required
def report_xlsx():
    from openpyxl import Workbook
    wb=Workbook();wb.active.title='Miembros';_sheet(wb.active,HEADERS,filtered_members())
    bio=BytesIO();wb.save(bio);bio.seek(0)
    audit('Exportar','Reporte',details='Miembros Excel filtrado');get_db().commit()
    return send_file(bio,as_attachment=True,download_name='reporte_miembros.xlsx',mimetype=XLSX_MIME)

@bp.route('/reports/members.pdf')
@login_required
def report_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph,SimpleDocTemplate,Spacer,Table,TableStyle
    styles=getSampleStyleSheet();cell=styles['BodyText'];cell.fontSize=7;cell.leading=9
    def p(v):return Paragraph(escape(str(v if v is not None else '')),cell)
    data=[[p(v) for v in r] for r in [HEADERS]+filtered_members()]
    table=Table(data,repeatRows=1,colWidths=[145,85,75,95,65,65,65,35,65])
    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.25,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP')]))
    bio=BytesIO();SimpleDocTemplate(bio,pagesize=landscape(A4),leftMargin=20,rightMargin=20).build([Paragraph('Sistema Iglesia Web · Reporte de miembros',styles['Heading2']),Spacer(1,12),table]);bio.seek(0)
    audit('Exportar','Reporte',details='Miembros PDF filtrado');get_db().commit()
    return send_file(bio,as_attachment=True,download_name='reporte_miembros.pdf',mimetype='application/pdf')


@bp.route('/reports/complete.xlsx')
@pastoral_required  # incluye todos los miembros y el historial: antes lo podía descargar cualquier usuario
def complete_xlsx():
    from openpyxl import Workbook
    db = get_db()
    wb = Workbook()
    wb.remove(wb.active)
    sets = [
        ('Miembros', f'SELECT {NAME_SQL},Cedula,Phone,Status,JoinDate,BaptismDate,ChurchRole FROM Members WHERE DeletedAt IS NULL ORDER BY 1 COLLATE NOCASE',
         ['Nombre', 'Cédula', 'Teléfono', 'Estado', 'Ingreso', 'Bautizo', 'Cargo']),
        ('Ministerios', "SELECT Name,Leader,Assistant,Notes FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name",
         ['Ministerio', 'Encargado', 'Asistente', 'Notas']),
        ('Actividad', 'SELECT CreatedAt,Action,Entity,Details FROM AuditLog ORDER BY Id DESC',
         ['Fecha', 'Acción', 'Módulo', 'Detalle']),
        ('Conversiones', "SELECT c.FullName,c.ConversionDate,coalesce(mi.Name,''),coalesce(u.FullName,''),coalesce(c.Notes,'') FROM Conversions c LEFT JOIN Ministries mi ON mi.Id=c.MinistryId LEFT JOIN Users u ON u.Id=c.RegisteredBy ORDER BY c.ConversionDate DESC",
         ['Nombre','Fecha','Ministerio','Registrado por','Observación']),
    ]
    for title, sql, heads in sets:
        _sheet(wb.create_sheet(title), heads, db.execute(sql).fetchall())
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    audit('Exportar', 'Reporte', details='Reporte completo Excel')
    db.commit()
    return send_file(bio, as_attachment=True, download_name='reporte_completo.xlsx', mimetype=XLSX_MIME)

def _baptism_rows():
    db=get_db(); sc,sp=scope('m')
    sql='''SELECT m.MemberCode,m.FullName,m.Phone,coalesce(mi.Name,'') MinistryName,m.BaptismDate,m.MinistryId
           FROM Members m LEFT JOIN Ministries mi ON mi.Id=m.MinistryId'''+where(sc,"m.BaptismDate IS NOT NULL AND m.BaptismDate<>''")
    rows=db.execute(sql+' ORDER BY m.BaptismDate DESC,m.FullName',sp).fetchall(); out=[]
    year=request.args.get('year',type=int); month=request.args.get('month',type=int); ministry=request.args.get('ministry',type=int); term=(request.args.get('q') or '').strip().lower()
    for r in rows:
        if year and not str(r['BaptismDate']).startswith(str(year)+'-'):continue
        if month and str(r['BaptismDate'])[5:7] != f'{month:02d}':continue
        if ministry and r['MinistryId']!=ministry:continue
        if term and term not in ((r['FullName'] or '')+' '+(r['MemberCode'] or '')).lower():continue
        out.append(r)
    return out

@bp.route('/reports/baptisms')
@login_required
def baptisms():
    rows=_baptism_rows(); db=get_db(); year=datetime.date.today().year
    sc,sp=scope('m'); all_rows=db.execute("SELECT m.BaptismDate FROM Members m"+where(sc,"m.BaptismDate IS NOT NULL AND m.BaptismDate<>''"),sp).fetchall()
    totals={'year':sum(1 for r in all_rows if str(r['BaptismDate']).startswith(str(year)+'-')),'all':len(all_rows)}
    years=sorted({int(str(r['BaptismDate'])[:4]) for r in all_rows if str(r['BaptismDate'])[:4].isdigit()}|{year},reverse=True)
    ministries=db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()
    return render_template('baptisms_report.html',title='BAUTIZADOS',rows=rows,totals=totals,years=years,ministries=ministries)

@bp.route('/reports/baptisms.xlsx')
@login_required
def baptisms_xlsx():
    from openpyxl import Workbook
    rows=[[r['MemberCode'],r['FullName'],r['Phone'],r['MinistryName'],r['BaptismDate']] for r in _baptism_rows()]
    wb=Workbook(); wb.active.title='Bautizados'; _sheet(wb.active,['Código MEM','Nombre','Teléfono','Ministerio','Fecha de Bautismo'],rows)
    bio=BytesIO(); wb.save(bio); bio.seek(0); audit('Exportar','Reporte',details='Bautizados Excel'); get_db().commit()
    return send_file(bio,as_attachment=True,download_name='reporte_bautizados.xlsx',mimetype=XLSX_MIME)

@bp.route('/reports/baptisms.pdf')
@login_required
def baptisms_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph,SimpleDocTemplate,Spacer,Table,TableStyle
    styles=getSampleStyleSheet(); cell=styles['BodyText']; cell.fontSize=8; cell.leading=10
    def pp(v):return Paragraph(escape(str(v or '')),cell)
    heads=['Código MEM','Nombre','Teléfono','Ministerio','Fecha de Bautismo']; rows=[[r['MemberCode'],r['FullName'],r['Phone'],r['MinistryName'],r['BaptismDate']] for r in _baptism_rows()]
    data=[[pp(v) for v in x] for x in [heads]+rows]; table=Table(data,repeatRows=1,colWidths=[75,180,100,150,100]); table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.25,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP')]))
    bio=BytesIO(); SimpleDocTemplate(bio,pagesize=landscape(A4),leftMargin=25,rightMargin=25).build([Paragraph('Sistema Iglesia Web · Bautizados',styles['Heading2']),Spacer(1,12),table]); bio.seek(0); audit('Exportar','Reporte',details='Bautizados PDF'); get_db().commit()
    return send_file(bio,as_attachment=True,download_name='reporte_bautizados.pdf',mimetype='application/pdf')


# V3.1.0: informes de asistencia (solo personal pastoral autorizado).
def _attendance_dashboard_data():
    from flask import abort
    from .security import is_pastoral
    from .attendance import SERVICES, _attendance_report
    if not is_pastoral(): abort(403)
    today=datetime.date.today()
    month=request.args.get('month',today.strftime('%Y-%m'))
    try:
        first=datetime.date.fromisoformat(month+'-01')
        if first.strftime('%Y-%m') != month: raise ValueError()
    except ValueError: abort(400)
    end=(first.replace(day=28)+datetime.timedelta(days=4)).replace(day=1)
    ministry=request.args.get('ministry',type=int)
    service=request.args.get('service','')
    if service and service not in SERVICES: abort(400)
    db=get_db()
    all_ministries=db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()
    params=[first.isoformat(),end.isoformat()]
    service_clause=' AND a.ServiceKey=?' if service else ''
    if service: params.append(service)
    ministry_clause=' AND m.MinistryId=?' if ministry else ''
    if ministry: params.append(ministry)
    attendees=db.execute('''SELECT a.ServiceDate,a.ServiceKey,a.MemberId,m.MinistryId,coalesce(mi.Name,'Sin ministerio') Ministry
      FROM Attendance a JOIN Members m ON m.Id=a.MemberId LEFT JOIN Ministries mi ON mi.Id=m.MinistryId
      WHERE a.ServiceDate>=? AND a.ServiceDate<? AND m.DeletedAt IS NULL'''+service_clause+ministry_clause,params).fetchall()
    vparams=[first.isoformat(),end.isoformat()]
    vclause=' AND ServiceKey=?' if service else ''
    if service: vparams.append(service)
    # Visitantes no tienen ministerio; solo se incluyen en la vista general.
    visitors=db.execute('SELECT ServiceDate,ServiceKey,FullName,Phone FROM AttendanceVisitors WHERE ServiceDate>=? AND ServiceDate<?'+vclause,vparams).fetchall() if not ministry else []
    sessions={}
    for a in attendees:
        key=(a['ServiceDate'],a['ServiceKey']); sessions.setdefault(key,{'members':set(),'visitors':0})['members'].add(a['MemberId'])
    for v in visitors:
        key=(v['ServiceDate'],v['ServiceKey']); sessions.setdefault(key,{'members':set(),'visitors':0})['visitors']+=1
    rows=[dict(date=d,service=SERVICES.get(k,(k,))[0],members=len(info['members']),visitors=info['visitors'],total=len(info['members'])+info['visitors']) for (d,k),info in sorted(sessions.items())]
    group={}
    for a in attendees:
        key=a['Ministry']; group.setdefault(key,set()).add(a['MemberId'])
    by_ministry=[dict(name=k,unique=len(v),records=sum(1 for a in attendees if a['Ministry']==k)) for k,v in sorted(group.items())]
    follow,_,fridays=_attendance_report()
    # Respetar filtro de ministerio para el seguimiento.
    selected_name=next((m['Name'] for m in all_ministries if m['Id']==ministry),None) if ministry else None
    alerts=[r for r in follow if r['alert'] and (not selected_name or r['ministry']==selected_name)]
    return dict(month=month,ministries=all_ministries,ministry=ministry,service=service,services=SERVICES,
       rows=rows,groups=by_ministry,alerts=alerts,fridays=fridays,
       total=sum(r['total'] for r in rows),members=len({a['MemberId'] for a in attendees}),
       visitors=len(visitors),sessions=len(rows))

@bp.route('/reports/attendance')
@pastoral_required
def attendance_dashboard():
    return render_template('attendance_dashboard.html',title='REPORTES DE ASISTENCIA',data=_attendance_dashboard_data())

@bp.route('/reports/attendance.xlsx')
@pastoral_required
def attendance_dashboard_xlsx():
    from openpyxl import Workbook
    d=_attendance_dashboard_data(); wb=Workbook(); ws=wb.active; ws.title='Resumen'
    _sheet(ws,['Indicador','Valor'],[['Mes',d['month']],['Cultos con registros',d['sessions']],['Asistencias (marcaciones)',d['total']],['Miembros únicos',d['members']],['Registros visitantes',d['visitors']],['Alertas pastorales',len(d['alerts'])]])
    _sheet(wb.create_sheet('Por culto'),['Fecha','Culto','Miembros','Visitantes','Total'],[[r['date'],r['service'],r['members'],r['visitors'],r['total']] for r in d['rows']])
    _sheet(wb.create_sheet('Ministerios'),['Ministerio','Miembros únicos','Asistencias'],[[r['name'],r['unique'],r['records']] for r in d['groups']])
    _sheet(wb.create_sheet('Seguimiento'),['MEM','Miembro','Ministerio','Viernes ausentes','Última asistencia'],[[r['code'],r['name'],r['ministry'],r['missed_fridays'],r['last']] for r in d['alerts']])
    bio=BytesIO();wb.save(bio);bio.seek(0)
    audit('Exportar','Reporte',details='Asistencia Excel '+d['month']);get_db().commit()
    return send_file(bio,as_attachment=True,download_name='asistencia_'+d['month']+'.xlsx',mimetype=XLSX_MIME)

@bp.route('/reports/attendance.pdf')
@pastoral_required
def attendance_dashboard_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
    d=_attendance_dashboard_data();bio=BytesIO();styles=getSampleStyleSheet()
    doc=SimpleDocTemplate(bio,pagesize=A4,leftMargin=35,rightMargin=35)
    story=[Paragraph('Iglesia de Dios de la Profecía - Verón Central',styles['Heading2']),Paragraph('Reporte de asistencia · '+d['month'],styles['Heading3']),Spacer(1,12)]
    def table(headers,rows):
        safe=lambda v:Paragraph(escape(str(v if v is not None else '')),styles['BodyText'])
        data=[[safe(x) for x in headers]]+[[safe(x) for x in row] for row in rows]
        t=Table(data,repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('GRID',(0,0),(-1,-1),.3,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP'),('FONTSIZE',(0,0),(-1,-1),8)]));return t
    story.extend([table(['Cultos','Marcaciones','Miembros únicos','Visitantes','Alertas'],[[d['sessions'],d['total'],d['members'],d['visitors'],len(d['alerts'])]]),Spacer(1,15),Paragraph('Por culto',styles['Heading3']),table(['Fecha','Culto','Miembros','Visitantes'],[[r['date'],r['service'],r['members'],r['visitors']] for r in d['rows']]),Spacer(1,15),Paragraph('Por ministerio',styles['Heading3']),table(['Ministerio','Miembros únicos','Asistencias'],[[r['name'],r['unique'],r['records']] for r in d['groups']]),Spacer(1,15),Paragraph('Seguimiento pastoral (acceso restringido)',styles['Heading3']),table(['MEM','Miembro','Ministerio','Viernes'],[[r['code'],r['name'],r['ministry'],r['missed_fridays']] for r in d['alerts']])])
    doc.build(story);bio.seek(0)
    audit('Exportar','Reporte',details='Asistencia PDF '+d['month']);get_db().commit()
    return send_file(bio,as_attachment=True,download_name='asistencia_'+d['month']+'.pdf',mimetype='application/pdf')
