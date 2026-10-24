"""Calendario / eventos: listado, alta, edición y eliminación.

Permisos:
* Administrador: todos los eventos.
* Usuario de ministerio: ve los eventos de su ministerio y los generales (sin
  ministerio); solo puede crear/editar/eliminar los de su propio ministerio.
"""
import datetime
import calendar
import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .db import audit, get_db, now_iso
from .security import (can_edit_event, clean, event_scope, is_admin, is_staff, login_required,
                       parse_date, where)

bp = Blueprint('events', __name__)

STATUSES = ['Próximo', 'Realizado', 'Cancelado']
_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
TODAY_SQL = "date('now','localtime')"

FIELDS = {'Name': 'Título', 'EventDate': 'Fecha', 'EventTime': 'Hora', 'Venue': 'Lugar',
          'MinistryId': 'Ministerio', 'Responsible': 'Responsable', 'Theme': 'Tema',
          'Status': 'Estado', 'Notes': 'Notas', 'Reminder': 'Recordatorio'}


def _ministries(db):
    return db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()


def _parse(form, ministry_ids, current_status=None):
    errors, d = [], {}
    d['Name'] = clean(form.get('Name'), 150)
    if not d['Name']:
        errors.append('El título del evento es obligatorio.')
    d['EventDate'], err = parse_date(form.get('EventDate'))
    if err:
        errors.append('Fecha: fecha no válida.')
    elif not d['EventDate']:
        errors.append('La fecha del evento es obligatoria.')
    t = clean(form.get('EventTime'), 5)
    if t and not _TIME_RE.match(t):
        errors.append('Hora: use el formato HH:MM.')
        t = ''
    d['EventTime'] = t or None
    for f, n in (('Venue', 150), ('Responsible', 150), ('Theme', 200), ('Notes', 2000), ('Reminder', 250)):
        d[f] = clean(form.get(f), n) or None
    allowed = STATUSES + ([current_status] if current_status and current_status not in STATUSES else [])
    d['Status'] = clean(form.get('Status'), 30) or 'Próximo'
    if d['Status'] not in allowed:
        errors.append('Estado: opción no válida.')
        d['Status'] = 'Próximo'
    if is_staff():
        raw = (form.get('MinistryId') or '').strip()
        if not raw:
            d['MinistryId'] = None
        elif raw.isdigit() and int(raw) in ministry_ids:
            d['MinistryId'] = int(raw)
        else:
            d['MinistryId'] = None
            errors.append('El ministerio seleccionado no existe.')
    else:
        d['MinistryId'] = g.user['MinistryId']
        if d['MinistryId'] is None:
            errors.append('Su usuario no tiene un ministerio asignado. Pida al administrador que lo asigne.')
    return d, errors


def _form(title, e, db, current_status=None):
    statuses = STATUSES + ([current_status] if current_status and current_status not in STATUSES else [])
    return render_template('event_form.html', title=title, e=e, ministries=_ministries(db),
                           statuses=statuses, locked=not is_staff())


@bp.route('/events')
@login_required
def events():
    db = get_db()
    esc, esp = event_scope('e')
    rows = db.execute(
        'SELECT e.Id,e.Name,e.EventDate,e.EventTime,e.Venue,e.Responsible,e.Reminder,e.Status,e.MinistryId,'
        'm.Name AS MinistryName FROM Events e LEFT JOIN Ministries m ON m.Id=e.MinistryId'
        + where(esc)
        # primero los que vienen (del más cercano al más lejano), después los pasados (el más reciente primero)
        + f' ORDER BY (date(e.EventDate)<{TODAY_SQL}),'
          f' CASE WHEN date(e.EventDate)>={TODAY_SQL} THEN e.EventDate END ASC, e.EventDate DESC, e.EventTime',
        esp).fetchall()
    items = [dict(r, can_edit=can_edit_event(r)) for r in rows]
    month=request.args.get('month') or datetime.date.today().strftime('%Y-%m')
    try: first=datetime.date.fromisoformat(month+'-01')
    except ValueError: first=datetime.date.today().replace(day=1)
    weeks=calendar.Calendar(firstweekday=0).monthdayscalendar(first.year,first.month)
    days={day:[x for x in items if x['EventDate']==first.replace(day=day).isoformat()] for week in weeks for day in week if day}
    return render_template('events.html', title='CALENDARIO & EVENTOS', rows=items, month=first.strftime('%Y-%m'),weeks=weeks,days=days,
                           today=datetime.date.today().isoformat())


@bp.route('/events/new', methods=['GET', 'POST'])
@login_required
def event_new():
    db = get_db()
    if request.method == 'POST':
        d, errors = _parse(request.form, {m['Id'] for m in _ministries(db)})
        if errors:
            for e in errors:
                flash(e)
            return _form('NUEVO EVENTO', d, db)
        cols = list(FIELDS)
        cur = db.execute(f'INSERT INTO Events(CreatedAt,{",".join(cols)}) VALUES(?,{",".join("?" * len(cols))})',
                         [now_iso()] + [d[c] for c in cols])
        audit('Crear', 'Evento', cur.lastrowid, f"Evento creado: {d['Name']} ({d['EventDate']})")
        db.commit()
        flash('Evento creado correctamente.')
        return redirect(url_for('events.events'))
    return _form('NUEVO EVENTO', {'Status': 'Próximo'}, db)


def _load_editable(db, i):
    row = db.execute('SELECT * FROM Events WHERE Id=?', (i,)).fetchone()
    if not can_edit_event(row):
        flash('No tiene permiso para modificar este evento.')
        return None
    return row


@bp.route('/events/<int:i>/edit', methods=['GET', 'POST'])
@login_required
def event_edit(i):
    db = get_db()
    row = _load_editable(db, i)
    if row is None:
        return redirect(url_for('events.events'))
    if request.method == 'POST':
        d, errors = _parse(request.form, {m['Id'] for m in _ministries(db)}, row['Status'])
        if errors:
            for e in errors:
                flash(e)
            merged = dict(row)
            merged.update(d)
            return _form('EDITAR EVENTO', merged, db, row['Status'])
        cols = list(FIELDS)
        changed = [FIELDS[c] for c in cols if (row[c] or None) != (d[c] or None)]
        db.execute(f'UPDATE Events SET {",".join(c + "=?" for c in cols)} WHERE Id=?', [d[c] for c in cols] + [i])
        audit('Editar', 'Evento', i, f"Evento actualizado: {d['Name']}. "
              + (('Cambios: ' + ', '.join(changed)) if changed else 'Sin cambios'))
        db.commit()
        flash('Evento actualizado correctamente.')
        return redirect(url_for('events.events'))
    return _form('EDITAR EVENTO', dict(row), db, row['Status'])


@bp.route('/events/<int:i>/delete', methods=['POST'])
@login_required
def event_delete(i):
    db = get_db()
    row = _load_editable(db, i)
    if row is None:
        return redirect(url_for('events.events'))
    db.execute('DELETE FROM Events WHERE Id=?', (i,))
    audit('Eliminar', 'Evento', i, f"Evento eliminado: {row['Name']} ({row['EventDate'] or 'sin fecha'})")
    db.commit()
    flash('Evento eliminado.')
    return redirect(url_for('events.events'))
