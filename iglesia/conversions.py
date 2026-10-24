"""Convertidos: registro, seguimiento, estadísticas y reportes."""
import datetime
from functools import wraps
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from .db import audit, get_db, now_iso, today_dr
from .security import clean, parse_date, is_pastoral, ROLE_MINISTRY

bp=Blueprint('conversions',__name__)

def _is_new_believers_manager():
    if not g.get('user') or g.user['Role'] != ROLE_MINISTRY or not g.user['MinistryId']:
        return False
    row=get_db().execute('SELECT Name FROM Ministries WHERE Id=?',(g.user['MinistryId'],)).fetchone()
    name=(row['Name'] if row else '').lower()
    return 'nuevo' in name and 'creyente' in name

def conversions_required(f):
    @wraps(f)
    def wrapper(*a,**k):
        if not g.get('user'):
            return redirect(url_for('auth.login'))
        if not (is_pastoral() or _is_new_believers_manager()):
            abort(403)
        return f(*a,**k)
    return wrapper

@bp.route('/conversions',methods=['GET','POST'])
@conversions_required
def conversions():
    db=get_db()
    if request.method=='POST':
        member_id=request.form.get('MemberId',type=int)
        member=db.execute('SELECT Id,FullName FROM Members WHERE Id=? AND DeletedAt IS NULL',(member_id,)).fetchone() if member_id else None
        fullname=(member['FullName'] if member else clean(request.form.get('FullName'),200))
        date,err=parse_date(request.form.get('ConversionDate'))
        if not fullname:
            flash('Escriba el nombre de la persona o seleccione un miembro existente.')
        elif err or not date:
            flash('La fecha de conversión no es válida.')
        else:
            cur=db.execute('INSERT INTO Conversions(MemberId,FullName,ConversionDate,MinistryId,RegisteredBy,Notes,CreatedAt,Phone) VALUES(?,?,?,?,?,?,?,?)',
                           (member_id,fullname,date,None,g.user['Id'],clean(request.form.get('Notes'),1000),now_iso(),clean(request.form.get('Phone'),50)))
            audit('Crear','Convertido',cur.lastrowid,f'{fullname} · {date}')
            db.commit(); flash('Convertido registrado correctamente.')
            return redirect(url_for('conversions.conversions'))
    members=db.execute('SELECT Id,MemberCode,FullName FROM Members WHERE DeletedAt IS NULL ORDER BY FullName').fetchall()
    q='''SELECT c.*,u.FullName UserName,m.MemberCode MemberCode FROM Conversions c LEFT JOIN Users u ON u.Id=c.RegisteredBy LEFT JOIN Members m ON m.Id=c.MemberId WHERE 1=1'''; params=[]
    if request.args.get('month',type=int): q+=' AND CAST(substr(c.ConversionDate,6,2) AS INTEGER)=?'; params.append(request.args.get('month',type=int))
    year=request.args.get('year',type=int)
    if year and year>=2026: q+=' AND CAST(substr(c.ConversionDate,1,4) AS INTEGER)=?'; params.append(year)
    if request.args.get('name'):
        term='%'+request.args['name'].strip().lower()+'%'
        q+=" AND (lower(c.FullName) LIKE ? OR lower(coalesce(c.Phone,'')) LIKE ? OR lower(coalesce(m.MemberCode,'')) LIKE ?)"
        params.extend([term,term,term])
    rows=db.execute(q+' ORDER BY c.ConversionDate DESC,c.Id DESC',params).fetchall()
    today=today_dr(); prev=(today.replace(day=1)-datetime.timedelta(days=1)).strftime('%Y-%m')
    totals={'month':db.execute("SELECT COUNT(*) FROM Conversions WHERE substr(ConversionDate,1,7)=?",(today.strftime('%Y-%m'),)).fetchone()[0],
            'previous':db.execute("SELECT COUNT(*) FROM Conversions WHERE substr(ConversionDate,1,7)=?",(prev,)).fetchone()[0],
            'year':db.execute("SELECT COUNT(*) FROM Conversions WHERE substr(ConversionDate,1,4)=?",(str(today.year),)).fetchone()[0],
            'all':db.execute('SELECT COUNT(*) FROM Conversions').fetchone()[0]}
    monthly=db.execute("SELECT substr(ConversionDate,1,7) ym,COUNT(*) total FROM Conversions GROUP BY substr(ConversionDate,1,7) ORDER BY ym DESC LIMIT 24").fetchall()
    years=list(range(2026,today.year+1))
    return render_template('conversions.html',title='CONVERTIDOS',members=members,rows=rows,totals=totals,monthly=monthly,today=today.isoformat(),years=years)

@bp.route('/conversions/<int:cid>/followup',methods=['POST'])
@conversions_required
def followup(cid):
    db=get_db(); date,err=parse_date(request.form.get('ContactDate')); typ=request.form.get('ContactType')
    if not db.execute('SELECT 1 FROM Conversions WHERE Id=?',(cid,)).fetchone(): abort(404)
    if err or not date or typ not in ('Llamada','WhatsApp','Visita','Otro'): flash('Complete correctamente el seguimiento.')
    else:
        db.execute('INSERT INTO ConversionFollowUps(ConversionId,ContactDate,ContactType,Status,Notes,RegisteredBy,CreatedAt) VALUES(?,?,?,?,?,?,?)',(cid,date,typ,clean(request.form.get('Status'),100),clean(request.form.get('Notes'),1000),g.user['Id'],now_iso()))
        audit('Seguimiento','Convertido',cid,f'{typ} · {date}'); db.commit(); flash('Seguimiento registrado.')
    return redirect(url_for('conversions.detail',cid=cid))

@bp.route('/conversions/<int:cid>')
@conversions_required
def detail(cid):
    db=get_db(); c=db.execute('SELECT c.*,u.FullName UserName FROM Conversions c LEFT JOIN Users u ON u.Id=c.RegisteredBy WHERE c.Id=?',(cid,)).fetchone()
    if not c: abort(404)
    followups=db.execute('''SELECT f.*,u.FullName UserName FROM ConversionFollowUps f LEFT JOIN Users u ON u.Id=f.RegisteredBy WHERE f.ConversionId=? ORDER BY f.ContactDate DESC,f.Id DESC''',(cid,)).fetchall()
    return render_template('conversion_detail.html',title='SEGUIMIENTO DEL CONVERTIDO',c=c,followups=followups,today=today_dr().isoformat())

@bp.route('/conversions.pdf')
@conversions_required
def conversions_pdf():
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Table,TableStyle,Paragraph,Spacer
    from flask import send_file
    rows=get_db().execute("SELECT FullName,ConversionDate,coalesce(Phone,''),coalesce(Notes,'') FROM Conversions ORDER BY ConversionDate DESC").fetchall()
    data=[['Nombre','Fecha','Teléfono','Observación']]+[list(r) for r in rows]
    table=Table(data,repeatRows=1,colWidths=[190,80,110,330]); table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.25,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP')]))
    bio=BytesIO(); SimpleDocTemplate(bio,pagesize=landscape(A4),leftMargin=20,rightMargin=20).build([Paragraph('Sistema Iglesia Web · Convertidos',getSampleStyleSheet()['Heading2']),Spacer(1,12),table]); bio.seek(0)
    return send_file(bio,as_attachment=True,download_name='convertidos.pdf',mimetype='application/pdf')
