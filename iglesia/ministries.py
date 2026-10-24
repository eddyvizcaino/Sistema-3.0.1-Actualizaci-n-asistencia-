"""Ministerios: listado (según permisos) y alta/edición/baja (solo administrador)."""
import sqlite3

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .db import audit, get_db, now_iso
from .security import admin_required, pastoral_required, clean, is_admin, is_staff, login_required

bp = Blueprint('ministries', __name__)

_LIST_SQL = ('''SELECT m.Id,m.Name,m.Leader,m.Assistant,m.Notes,m.LeaderMemberId,m.Assistant1MemberId,m.Assistant2MemberId,
 COALESCE(l.FirstName || ' ' || l.LastName, l.FullName, m.Leader) AS LeaderName,
 COALESCE(a1.FirstName || ' ' || a1.LastName, a1.FullName, m.Assistant) AS Assistant1Name,
 COALESCE(a2.FirstName || ' ' || a2.LastName, a2.FullName, '') AS Assistant2Name,
 COUNT(mem.Id) AS Total FROM Ministries m
 LEFT JOIN Members mem ON mem.MinistryId=m.Id AND mem.DeletedAt IS NULL AND COALESCE(mem.Status,'Activo')='Activo'
 LEFT JOIN Members l ON l.Id=m.LeaderMemberId
 LEFT JOIN Members a1 ON a1.Id=m.Assistant1MemberId
 LEFT JOIN Members a2 ON a2.Id=m.Assistant2MemberId''')
_GROUP = ''' GROUP BY m.Id,m.Name,m.Leader,m.Assistant,m.Notes,m.LeaderMemberId,m.Assistant1MemberId,m.Assistant2MemberId,
 l.FirstName,l.LastName,l.FullName,a1.FirstName,a1.LastName,a1.FullName,a2.FirstName,a2.LastName,a2.FullName ORDER BY m.Name''' 


def _form_data():
    d = {k: clean(request.form.get(k), 300 if k != 'Notes' else 2000) for k in ('Name','Notes')}
    for k in ('LeaderMemberId','Assistant1MemberId','Assistant2MemberId'):
        raw=(request.form.get(k) or '').strip(); d[k]=int(raw) if raw.isdigit() else None
    return d

def _member_options(db):
    return db.execute("SELECT Id,FirstName,LastName,FullName,MemberCode FROM Members WHERE DeletedAt IS NULL ORDER BY FirstName,LastName,FullName").fetchall()

def _valid_assignments(db, d):
    ids=[d[k] for k in ('LeaderMemberId','Assistant1MemberId','Assistant2MemberId') if d[k]]
    if len(ids)!=len(set(ids)): return False, 'Una misma persona no puede ocupar dos encargaturas del mismo ministerio.'
    if ids:
        marks=','.join('?' for _ in ids); n=db.execute(f'SELECT COUNT(*) FROM Members WHERE DeletedAt IS NULL AND Id IN ({marks})', ids).fetchone()[0]
        if n != len(ids): return False, 'Seleccione encargados que sean miembros existentes.'
    return True, None

def _member_name(db, mid):
    if not mid: return ''
    r=db.execute('SELECT FirstName,LastName,FullName FROM Members WHERE Id=?',(mid,)).fetchone()
    return ((r['FirstName'] or '')+' '+(r['LastName'] or '')).strip() or (r['FullName'] or '') if r else ''


@bp.route('/ministries')
@login_required
def ministries():
    db = get_db()
    # V2.9.8.3 CORRECCION 2: leer primero TODOS los ministerios existentes.
    # El filtrado del registro técnico se hace en Python para no ocultar registros
    # reales por diferencias de esquema/driver entre SQLite y PostgreSQL.
    all_rows = db.execute("SELECT Id,Name,Leader,Assistant,Notes FROM Ministries ORDER BY Name").fetchall()
    visible = [m for m in all_rows if (m['Name'] or '').strip() != '__COMUNIDADES_ORACION__']
    if not is_staff():
        visible = [m for m in visible if m['Id'] == g.user['MinistryId']]
    rows = []
    for m in visible:
        total = db.execute("""SELECT COUNT(*) FROM Members
            WHERE MinistryId=? AND DeletedAt IS NULL
              AND COALESCE(Status,'Activo')='Activo'""", (m['Id'],)).fetchone()[0]
        rows.append({
            'Id': m['Id'], 'Name': m['Name'], 'Leader': m['Leader'],
            'Assistant': m['Assistant'], 'Notes': m['Notes'],
            'LeaderName': m['Leader'] or '', 'Assistant1Name': m['Assistant'] or '',
            'Assistant2Name': '', 'Total': total
        })
    return render_template('ministries.html', title='MINISTERIOS', rows=rows)


@bp.route('/ministries/new', methods=['GET', 'POST'])
@pastoral_required
def ministry_new():
    db = get_db()
    if request.method == 'POST':
        d = _form_data()
        if not d['Name']:
            flash('El nombre del ministerio es obligatorio.')
        elif db.execute('SELECT 1 FROM Ministries WHERE lower(Name)=lower(?)', (d['Name'],)).fetchone():
            flash('Ya existe un ministerio con ese nombre.')
        elif not _valid_assignments(db,d)[0]:
            flash(_valid_assignments(db,d)[1])
        else:
            leader=_member_name(db,d['LeaderMemberId']); assistant=_member_name(db,d['Assistant1MemberId'])
            cur = db.execute('INSERT INTO Ministries(Name,Leader,Assistant,Notes,LeaderMemberId,Assistant1MemberId,Assistant2MemberId) VALUES(?,?,?,?,?,?,?)',
                             (d['Name'], leader, assistant, d['Notes'],d['LeaderMemberId'],d['Assistant1MemberId'],d['Assistant2MemberId']))
            audit('Crear', 'Ministerio', cur.lastrowid, f"Ministerio creado: {d['Name']}")
            db.commit()
            flash('Ministerio creado correctamente.')
            return redirect(url_for('ministries.ministries'))
        return render_template('ministry_form.html', title='CREAR MINISTERIO', m=d, member_options=_member_options(db))
    return render_template('ministry_form.html', title='CREAR MINISTERIO', m={}, member_options=_member_options(db))


@bp.route('/ministries/<int:mid>/edit', methods=['GET', 'POST'])
@pastoral_required
def ministry_edit(mid):
    db = get_db()
    row = db.execute('SELECT * FROM Ministries WHERE Id=?', (mid,)).fetchone()
    if not row:
        flash('Ministerio no encontrado.')
        return redirect(url_for('ministries.ministries'))
    if request.method == 'POST':
        d = _form_data()
        if not d['Name']:
            flash('El nombre del ministerio es obligatorio.')
        elif db.execute('SELECT 1 FROM Ministries WHERE lower(Name)=lower(?) AND Id<>?', (d['Name'], mid)).fetchone():
            flash('Ya existe otro ministerio con ese nombre.')
        elif not _valid_assignments(db,d)[0]:
            flash(_valid_assignments(db,d)[1])
        else:
            before = ' | '.join(str(row[k] or '') for k in ('Name','Leader','Assistant','Notes'))
            leader=_member_name(db,d['LeaderMemberId']); assistant=_member_name(db,d['Assistant1MemberId'])
            after = ' | '.join((d['Name'],leader,assistant,d['Notes']))
            db.execute('UPDATE Ministries SET Name=?,Leader=?,Assistant=?,Notes=?,LeaderMemberId=?,Assistant1MemberId=?,Assistant2MemberId=? WHERE Id=?',
                       (d['Name'],leader,assistant,d['Notes'],d['LeaderMemberId'],d['Assistant1MemberId'],d['Assistant2MemberId'],mid))
            audit('Editar', 'Ministerio', mid, f'Antes: {before} | Después: {after}')
            db.commit()
            flash('Ministerio actualizado correctamente.')
            return redirect(url_for('ministries.ministries'))
        merged = dict(row)
        merged.update(d)
        return render_template('ministry_form.html', title='EDITAR MINISTERIO', m=merged, member_options=_member_options(db))
    return render_template('ministry_form.html', title='EDITAR MINISTERIO', m=dict(row), member_options=_member_options(db))


@bp.route('/ministries/<int:mid>/delete', methods=['POST'])
@pastoral_required
def ministry_delete(mid):
    db = get_db()
    row = db.execute('SELECT * FROM Ministries WHERE Id=?', (mid,)).fetchone()
    if not row:
        flash('Ministerio no encontrado.')
        return redirect(url_for('ministries.ministries'))
    n_members = db.execute('SELECT COUNT(*) FROM Members WHERE MinistryId=?', (mid,)).fetchone()[0]
    n_users = db.execute('SELECT COUNT(*) FROM Users WHERE MinistryId=?', (mid,)).fetchone()[0]
    n_events = db.execute('SELECT COUNT(*) FROM Events WHERE MinistryId=?', (mid,)).fetchone()[0]
    if n_members or n_users or n_events:
        flash(f'No se puede borrar este ministerio: tiene {n_members} miembro(s), {n_users} usuario(s) '
              f'y {n_events} evento(s) asignado(s). Reasígnalos primero.')
        return redirect(url_for('ministries.ministries'))
    try:
        db.execute('DELETE FROM Ministries WHERE Id=?', (mid,))
        audit('Borrar', 'Ministerio', mid, f"Ministerio eliminado: {row['Name']}")
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash('No se puede borrar: el ministerio todavía está en uso.')
        return redirect(url_for('ministries.ministries'))
    flash('Ministerio eliminado correctamente.')
    return redirect(url_for('ministries.ministries'))

@bp.route('/ministries/<int:mid>/add-member', methods=['GET', 'POST'])
@login_required
def add_member(mid):
    db = get_db()
    ministry = db.execute('SELECT * FROM Ministries WHERE Id=?', (mid,)).fetchone()
    if not ministry:
        flash('Ministerio no encontrado.')
        return redirect(url_for('ministries.ministries'))
    if not is_staff() and g.user['MinistryId'] != mid:
        from flask import abort
        abort(403)
    if request.method == 'POST':
        member_id = request.form.get('member_id', type=int)
        member = db.execute('SELECT Id,FullName,MinistryId,DeletedAt FROM Members WHERE Id=?', (member_id,)).fetchone()
        if not member or member['DeletedAt'] is not None:
            flash('Miembro no encontrado.')
        elif member['MinistryId'] == mid:
            flash('Ese miembro ya pertenece a este ministerio.')
        elif member['MinistryId'] is not None:
            flash('Ese miembro ya pertenece a otro ministerio. El Administrador debe realizar el cambio.')
        else:
            db.execute('UPDATE Members SET MinistryId=? WHERE Id=?', (mid, member_id))
            db.execute('INSERT INTO MemberHistory(MemberId,UserId,Action,Details,CreatedAt) VALUES(?,?,?,?,?)',
                       (member_id, g.user['Id'], 'Ministerio', 'Agregado al ministerio: ' + ministry['Name'], now_iso()))
            audit('Agregar miembro', 'Ministerio', mid, f"{member['FullName']} agregado a {ministry['Name']}")
            db.commit(); flash('Miembro agregado al ministerio sin duplicar su ficha.')
            return redirect(url_for('ministries.ministries'))
    # Para Nuevos Creyentes se identifican los convertidos pendientes sin crear
    # una segunda ficha. La asignación sigue usando Members.MinistryId.
    is_new_believers = 'nuevo' in (ministry['Name'] or '').lower() and 'creyente' in (ministry['Name'] or '').lower()
    if is_new_believers:
        available = db.execute("""SELECT mem.Id,coalesce(nullif(trim(coalesce(mem.FirstName,'')||' '||coalesce(mem.LastName,'')),''),mem.FullName) AS Name,
                                      mem.Cedula, MAX(c.ConversionDate) AS ConversionDate
                               FROM Members mem LEFT JOIN Conversions c ON c.MemberId=mem.Id
                               WHERE mem.MinistryId IS NULL AND mem.DeletedAt IS NULL
                               GROUP BY mem.Id,mem.FirstName,mem.LastName,mem.FullName,mem.Cedula
                               ORDER BY CASE WHEN MAX(c.ConversionDate) IS NULL THEN 1 ELSE 0 END, MAX(c.ConversionDate) DESC, Name COLLATE NOCASE""").fetchall()
    else:
        available = db.execute("SELECT Id,coalesce(nullif(trim(coalesce(FirstName,'')||' '||coalesce(LastName,'')),''),FullName) AS Name,Cedula,NULL AS ConversionDate FROM Members WHERE MinistryId IS NULL AND DeletedAt IS NULL ORDER BY Name COLLATE NOCASE").fetchall()
    return render_template('ministry_add_member.html', title='AGREGAR MIEMBRO', ministry=ministry, available=available,
                           is_new_believers=is_new_believers)
