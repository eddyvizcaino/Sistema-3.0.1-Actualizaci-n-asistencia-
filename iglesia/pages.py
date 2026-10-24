"""Inicio (panel), calendario/eventos y cumpleaños."""
import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .db import audit, get_db, now_iso, today_dr
from .security import NAME_SQL, clean, event_scope, is_admin, is_staff, login_required, parse_date, scope, where

bp = Blueprint('pages', __name__)

PRIORITIES = ['Normal', 'Alta', 'Urgente']



@bp.route('/')
@login_required
def home():
    db = get_db()
    sc, sp = scope()

    def count(table, *extra):
        return db.execute(f'SELECT COUNT(*) FROM {table}' + where(sc, *extra), sp).fetchone()[0]

    stats = {
        'total': count('Members', "COALESCE(Status,'Activo')='Activo'"),
        'inactive': count('Members', "Status='Inactivo'"),
        'baptized_year': count('Members', "BaptismDate IS NOT NULL AND BaptismDate<>'' AND substr(BaptismDate,1,4)='" + str(today_dr().year) + "'"),
        'active': count('Members', "Status='Activo'"),
        'ministries': (sum(1 for r in db.execute("SELECT Name FROM Ministries").fetchall() if (r['Name'] or '').strip() != '__COMUNIDADES_ORACION__') if is_staff() else 1),
        'new_month': count('Members', "substr(JoinDate,1,7)='" + today_dr().strftime('%Y-%m') + "'"),
        'conversions_month': db.execute("SELECT COUNT(*) FROM Conversions WHERE substr(ConversionDate,1,7)=?", (today_dr().strftime('%Y-%m'),)).fetchone()[0],
    }
    # V3.0: usar la misma fuente de datos visible que el módulo MINISTERIOS.
    # No usar LIKE '__%' porque '_' es comodín SQL y ocultaba nombres normales.
    ministry_rows = db.execute("SELECT Id,Name FROM Ministries ORDER BY Name").fetchall()
    ministry_rows = [m for m in ministry_rows if (m['Name'] or '').strip() != '__COMUNIDADES_ORACION__']
    if not is_staff():
        ministry_rows = [m for m in ministry_rows if m['Id'] == g.user['MinistryId']]
    per_ministry = []
    for m in ministry_rows:
        total = db.execute("""SELECT COUNT(*) FROM Members
            WHERE MinistryId=? AND DeletedAt IS NULL
              AND COALESCE(Status,'Activo')='Activo'""", (m['Id'],)).fetchone()[0]
        per_ministry.append({'Id': m['Id'], 'Name': m['Name'], 'Total': total})
    today=today_dr();celebrations=[]
    for person in db.execute('SELECT FullName,BirthDate,BaptismDate FROM Members'+where(sc),sp).fetchall():
        for field,label in [('BirthDate','Cumpleaños')]:
            try:
                original=datetime.date.fromisoformat(person[field])
                def occurrence(year):
                    try:return original.replace(year=year)
                    except ValueError:return datetime.date(year,2,28)
                nxt=occurrence(today.year)
                if nxt<today:nxt=occurrence(today.year+1)
                delta=(nxt-today).days
                if delta<=30:celebrations.append((delta,person['FullName'],label,nxt.isoformat()))
            except (ValueError,TypeError):pass
    return render_template('home.html', title='INICIO', stats=stats, per_ministry=per_ministry,celebrations=sorted(celebrations)[:10])


@bp.route('/birthdays')
@login_required
def birthdays():
    db = get_db()
    sc, sp = scope()
    rows = db.execute(
        f"SELECT {NAME_SQL} AS Name, m.BirthDate, COALESCE(mi.Name,'Sin ministerio') AS GroupName, m.Phone, m.MinistryId FROM Members m LEFT JOIN Ministries mi ON mi.Id=m.MinistryId"
        + where(sc.replace('DeletedAt','m.DeletedAt').replace('MinistryId','m.MinistryId'), "m.BirthDate IS NOT NULL AND m.BirthDate<>''"), sp).fetchall()
    today = today_dr()

    def occurrence(value, year):
        born = datetime.date.fromisoformat(value)
        try:
            return born.replace(year=year)
        except ValueError:  # 29 de febrero en año no bisiesto
            return datetime.date(year, 2, 28)

    def birthday_info(row):
        try:
            born = datetime.date.fromisoformat(row['BirthDate'])
            this_year = occurrence(row['BirthDate'], today.year)
            nxt = this_year if this_year >= today else occurrence(row['BirthDate'], today.year + 1)
            prev = this_year if this_year <= today else occurrence(row['BirthDate'], today.year - 1)
            return {
                'row': row,
                'born': born,
                'next': nxt,
                'days': (nxt - today).days,
                'since': (today - prev).days,
                'day_month': nxt.strftime('%d/%m'),
                'month': nxt.month,
            }
        except (ValueError, TypeError):
            return None

    info = [x for x in (birthday_info(r) for r in rows) if x]
    info.sort(key=lambda x: x['days'])
    today_birthdays = [x for x in info if x['days'] == 0]
    recent = sorted([x for x in info if 1 <= x['since'] <= 7], key=lambda x: x['since'])
    next7 = [x for x in info if 1 <= x['days'] <= 7]
    current_month = [x for x in info if x['born'].month == today.month]
    current_month.sort(key=lambda x: x['born'].day)

    month_names = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
    annual = []
    for month in range(1, 13):
        members = []
        for x in info:
            try:
                born = datetime.date.fromisoformat(x['row']['BirthDate'])
                if born.month == month:
                    members.append(x)
            except (ValueError, TypeError):
                pass
        members.sort(key=lambda x: int(x['row']['BirthDate'][8:10]))
        annual.append((month, month_names[month-1], members))

    return render_template('birthdays.html', title='CUMPLEAÑOS', today_birthdays=today_birthdays,
                           recent=recent, next7=next7, current_month=current_month, annual=annual,
                           month_names=month_names)
