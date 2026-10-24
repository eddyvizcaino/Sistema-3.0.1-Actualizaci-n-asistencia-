"""Miembros: listado, registro, edición, eliminación y perfil."""
import datetime
import os
import sqlite3
import uuid
import secrets
import re
import unicodedata
from io import BytesIO

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, url_for, jsonify, abort, send_file)

from .db import audit, get_db, now_iso, table_exists
from .security import (NAME_SQL, can_access_member, clean, is_admin, login_required,
                       normalize_cedula, parse_date, scope, where, is_staff, admin_required)

bp = Blueprint('members', __name__)

GROUPS = ['Caballeros', 'Damas', 'Adolescentes', 'Niños']
STATUSES = ['Activo', 'Inactivo', 'Descarriado', 'Trasladado', 'Fuera de la zona', 'Fallecido']
CHURCH_ROLES = ['Encargado', 'Asistente', 'Secretario', 'Tesorero', 'Miembro', 'Otro']
FOLLOWUPS = ['Nuevo', 'Activo', 'En seguimiento', 'Inactivo', 'Trasladado']
MARITAL_STATUSES = ['Casado/a', 'Soltero/a', 'Viudo/a', 'Unión Libre']

MAX_PHOTO_BYTES = 5 * 1024 * 1024

# Campos editables (columna -> etiqueta). Se usa para validar, guardar y para
# registrar qué cambió en el historial del miembro.
FIELDS = {
    'FirstName': 'Nombres', 'LastName': 'Apellidos', 'Cedula': 'Cédula',
    'BirthDate': 'Fecha de nacimiento', 'MaritalStatus': 'Estado civil', 'Profession': 'Profesión', 'GroupName': 'Grupo', 'Phone': 'Teléfono',
    'Address': 'Dirección', 'Status': 'Estado', 'MinistryId': 'Ministerio',
    'JoinDate': 'Fecha de ingreso', 'Baptized': 'Bautizado', 'BaptismDate': 'Fecha de bautizo',
    'ChurchRole': 'Cargo', 'GuardianFirstName': 'Nombres del responsable',
    'GuardianLastName': 'Apellidos del responsable', 'GuardianPhone': 'Teléfono del responsable',
    'LastContact': 'Último contacto', 'FollowUpStatus': 'Estado de seguimiento',
    'FollowUpNotes': 'Notas de seguimiento', 'Notes': 'Observaciones',
    'FatherName': 'Nombre del padre', 'MotherName': 'Nombre de la madre',
    'TutorName': 'Nombre del tutor', 'SpouseMemberId': 'Cónyuge miembro',
    'SpouseExternalName': 'Cónyuge externo',
}
DATE_FIELDS = ('BirthDate', 'JoinDate', 'BaptismDate', 'LastContact')

def _public_base():
    base = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')
    if not base:
        base = request.url_root.rstrip('/')
        if current_app.config.get('SESSION_COOKIE_SECURE'):
            base = base.replace('http://', 'https://', 1)
    return base

def _active_registration_token(create=False):
    db = get_db()
    now = now_iso()
    row = db.execute('SELECT * FROM PublicRegistrationLinks WHERE Active=1 AND ExpiresAt>? ORDER BY Id DESC LIMIT 1', (now,)).fetchone()
    if row or not create:
        return row
    token = secrets.token_urlsafe(32)
    expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)).isoformat()
    uid = g.user['Id'] if g.get('user') else None
    db.execute('INSERT INTO PublicRegistrationLinks(Token,ExpiresAt,Active,CreatedAt,CreatedBy) VALUES(?,?,?,?,?)',
               (token, expires, 1, now, uid))
    db.commit()
    return db.execute('SELECT * FROM PublicRegistrationLinks WHERE Token=?', (token,)).fetchone()

def registration_link():
    row = _active_registration_token(create=True)
    return _public_base() + url_for('members.public_registration', token=row['Token'])

def _valid_public_token(token):
    token = (token or '').strip()
    if not token or len(token) > 128:
        return False
    row = get_db().execute('SELECT Id FROM PublicRegistrationLinks WHERE Token=? AND Active=1 AND ExpiresAt>?',
                           (token, now_iso())).fetchone()
    return row is not None


def _public_rate_limited(limit=12, minutes=15):
    # Límite persistente por IP; funciona también con varios workers de Gunicorn.
    db = get_db()
    ip = (request.remote_addr or 'unknown')[:64]
    since = (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).isoformat(timespec='seconds')
    n = db.execute('SELECT COUNT(*) FROM RateLimitLog WHERE Scope=? AND KeyValue=? AND CreatedAt>?',
                   ('member_public', ip, since)).fetchone()[0]
    if n >= limit:
        return True
    db.execute('INSERT INTO RateLimitLog(Scope,KeyValue,CreatedAt) VALUES(?,?,?)',
               ('member_public', ip, now_iso()))
    db.execute('DELETE FROM RateLimitLog WHERE CreatedAt<?',
               ((datetime.datetime.now() - datetime.timedelta(days=2)).isoformat(timespec='seconds'),))
    db.commit()
    return False


@bp.route('/registro-miembros', methods=['GET', 'POST'])
def public_registration():
    token = request.args.get('token', '')
    if not _valid_public_token(token):
        abort(404)
    if request.method == 'POST':
        if _public_rate_limited():
            abort(429)
        if request.form.get('website'):
            return render_template('member_public.html', sent=True)
        first = clean(request.form.get('FirstName'), 80)
        last = clean(request.form.get('LastName'), 80)
        phone = clean(request.form.get('Phone'), 25)
        cedula = normalize_cedula(request.form.get('Cedula'))
        birth, error = parse_date(request.form.get('BirthDate'))
        group = clean(request.form.get('GroupName'), 40)
        if not first or not last or not phone or not re.fullmatch(r'[0-9+() .-]{7,25}', phone) or error or group not in GROUPS:
            flash('Revise nombres, apellidos, teléfono, fecha y grupo.')
        elif birth and birth > datetime.date.today().isoformat():
            flash('La fecha de nacimiento no puede ser futura.')
        else:
            get_db().execute('INSERT INTO MemberRegistration(FirstName,LastName,Cedula,BirthDate,Phone,Address,GroupName,GuardianPhone,CreatedAt) VALUES(?,?,?,?,?,?,?,?,?)',
                             (first, last, cedula, birth, phone, clean(request.form.get('Address'), 250),
                              group, clean(request.form.get('GuardianPhone'), 40), now_iso()))
            get_db().commit()
            return render_template('member_public.html', sent=True)
    return render_template('member_public.html', sent=False, groups=GROUPS)


@bp.route('/registro-miembros/link/renovar', methods=['POST'])
@admin_required
def renew_registration_link():
    db = get_db()
    db.execute('UPDATE PublicRegistrationLinks SET Active=0 WHERE Active=1')
    db.commit()
    registration_link()
    audit('Renovar link público', 'Seguridad', None, 'QR & LINK de registro')
    flash('El enlace anterior fue revocado y se generó uno nuevo con vigencia de 30 días.')
    return redirect(url_for('members.registrations'))


@bp.route('/registro-miembros/qr.svg')
@admin_required
def registration_qr():
    import qrcode
    from qrcode.image.svg import SvgPathImage
    output = BytesIO()
    qrcode.make(registration_link(), image_factory=SvgPathImage, box_size=8, border=4).save(output)
    output.seek(0)
    return send_file(output, mimetype='image/svg+xml',
                     as_attachment=request.args.get('download') == '1',
                     download_name='registro-miembros.svg')


@bp.route('/registro-miembros/solicitudes', methods=['GET', 'POST'])
@admin_required
def registrations():
    db = get_db()
    if request.method == 'POST':
        rid = request.form.get('registration_id', type=int)
        action = request.form.get('action')
        if not rid or action not in ('approve', 'reject'):
            abort(400)
        if getattr(db, 'is_postgres', False):
            db.execute('SELECT pg_advisory_xact_lock(276001)')
        else:
            db.execute('BEGIN IMMEDIATE')
        pending = db.execute('SELECT * FROM MemberRegistration WHERE Id=?', (rid,)).fetchone()
        if not pending:
            db.rollback()
            flash('La solicitud ya fue procesada.')
            return redirect(url_for('members.registrations'))
        if action == 'approve':
            d, errors = parse_form(dict(pending), {r['Id'] for r in _ministries(db)})
            if find_duplicate(db, d['Cedula']):
                errors.append('Ya existe un miembro con esa cédula.')
            if errors:
                db.rollback()
                for error in errors: flash(error)
                return redirect(url_for('members.registrations'))
            full, guardian = _row_values(d)
            cols = [c for c in FIELDS if c in d]
            cur = db.execute(f'INSERT INTO Members(FullName,GuardianName,CreatedAt,{",".join(cols)}) '
                             f'VALUES(?,?,?,{",".join("?" * len(cols))})',
                             [full, guardian, now_iso()] + [d[c] for c in cols])
            db.execute('INSERT INTO MemberHistory(MemberId,UserId,Action,Details,CreatedAt) VALUES(?,?,?,?,?)',
                       (cur.lastrowid, g.user['Id'], 'Crear', 'Solicitud pública aprobada', now_iso()))
            audit('Aprobar solicitud', 'Miembro', cur.lastrowid, full)
        else:
            pending_name = (pending['FullName'] if 'FullName' in pending.keys() else '') or f'Solicitud #{rid}'
            audit('Rechazar solicitud', 'Solicitud de miembro', rid, str(pending_name))
        db.execute('DELETE FROM MemberRegistration WHERE Id=?', (rid,))
        db.commit()
        flash('Solicitud aprobada.' if action == 'approve' else 'Solicitud descartada.')
        return redirect(url_for('members.registrations'))
    pending = db.execute('SELECT * FROM MemberRegistration ORDER BY Id').fetchall()
    return render_template('member_registrations.html', title='SOLICITUDES DE MIEMBROS',
                           pending=pending, link=registration_link())


# ------------------------------------------------------------------- fotos
def _image_ext(head):
    """Tipo real según los primeros bytes (no se confía en el nombre del archivo)."""
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    return None


def save_photo(file):
    """Guarda la foto subida. Devuelve (nombre_archivo, error). Sin archivo -> (None, None)."""
    if not file or not file.filename:
        return None, None
    data = file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        return None, 'La foto supera los 5 MB.'
    from PIL import Image, ImageOps, UnidentifiedImageError
    from io import BytesIO
    try:
        with Image.open(BytesIO(data)) as im:
            if im.width * im.height > 25000000:
                return None, 'La foto es demasiado grande.'
            im = ImageOps.exif_transpose(im).convert('RGB')
            im = ImageOps.fit(im, (600, 600))
            name = uuid.uuid4().hex + '.jpg'
            import base64
            output = BytesIO()
            im.save(output, format='JPEG', quality=85, optimize=True)
            get_db().execute('INSERT INTO Photos(Name,Data) VALUES(?,?)', (name, base64.b64encode(output.getvalue()).decode('ascii')))
        return name, None
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, 'La foto debe ser una imagen JPG, PNG o WEBP válida.'


def delete_photo(name):
    if name:
        get_db().execute('DELETE FROM Photos WHERE Name=?', (name,))
        get_db().commit()


@bp.route('/members/<int:i>/photo')
@login_required
def photo(i):
    from flask import abort, send_file
    from io import BytesIO
    import base64
    row = get_db().execute('SELECT * FROM Members WHERE Id=?', (i,)).fetchone()
    if not can_access_member(row):
        abort(403)
    item = get_db().execute('SELECT Data FROM Photos WHERE Name=?', (row['ProfilePhoto'],)).fetchone()
    if not item:
        abort(404)
    return send_file(BytesIO(base64.b64decode(item['Data'])), mimetype='image/jpeg')


# -------------------------------------------------------------- validación
def _ministries(db):
    # V3.0 FIX: '_' es comodín en LIKE. Comparar el nombre técnico exacto evita ocultar todos los ministerios.
    return db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()


def parse_form(form, ministry_ids):
    """Devuelve (datos_limpios, lista_de_errores)."""
    errors, d = [], {}
    d['FirstName'] = clean(form.get('FirstName'), 80)
    d['LastName'] = clean(form.get('LastName'), 80)
    if not d['FirstName']:
        errors.append('Los nombres son obligatorios.')
    if not d['LastName']:
        errors.append('Los apellidos son obligatorios.')
    d['Cedula'] = normalize_cedula(form.get('Cedula'))
    for f in DATE_FIELDS:
        d[f], err = parse_date(form.get(f))
        if err:
            errors.append(f'{FIELDS[f]}: {err}.')
    if d['BirthDate'] and d['BirthDate'] > datetime.date.today().isoformat():
        errors.append('La fecha de nacimiento no puede ser futura.')

    def choice(field, allowed, default):
        v = clean(form.get(field), 40) or default
        if v not in allowed:
            errors.append(f'{FIELDS[field]}: opción no válida.')
            return default
        return v

    d['MaritalStatus'] = choice('MaritalStatus', MARITAL_STATUSES, 'Soltero/a')
    d['GroupName'] = choice('GroupName', [''] + GROUPS, '')
    d['Status'] = choice('Status', STATUSES, 'Activo')
    baptized = clean(form.get('Baptized'), 10)
    if baptized not in ('', 'Sí', 'No'):
        errors.append('Bautizado: opción no válida.')
        baptized = ''
    d['Baptized'] = baptized
    d['ChurchRole'] = choice('ChurchRole', CHURCH_ROLES, 'Miembro')
    d['FollowUpStatus'] = choice('FollowUpStatus', FOLLOWUPS, 'Activo')
    for f, n in (('Profession', 120), ('Phone', 40), ('Address', 250), ('GuardianFirstName', 80), ('GuardianLastName', 80),
                 ('GuardianPhone', 40), ('FatherName', 160), ('MotherName', 160),
                 ('TutorName', 160), ('SpouseExternalName', 160), ('FollowUpNotes', 2000), ('Notes', 2000)):
        d[f] = clean(form.get(f), n)

    # Los campos familiares solo se editan para menores de edad.
    age = None
    if d['BirthDate']:
        born = datetime.date.fromisoformat(d['BirthDate'])
        today = datetime.date.today()
        age = today.year-born.year-((today.month,today.day)<(born.month,born.day))
    if age is None or age >= 18:
        for key in ('FatherName','MotherName','TutorName'):
            d.pop(key, None)  # Mantener información histórica del adulto.
    spouse_raw = (form.get('SpouseMemberId') or '').strip()
    if d['MaritalStatus'] == 'Casado/a':
        if spouse_raw and not spouse_raw.isdigit():
            errors.append('Seleccione un cónyuge válido.')
        d['SpouseMemberId'] = int(spouse_raw) if spouse_raw.isdigit() else None
        if d['SpouseMemberId']:
            found = get_db().execute('SELECT Id,SpouseMemberId FROM Members WHERE Id=? AND DeletedAt IS NULL', (d['SpouseMemberId'],)).fetchone()
            if not found:
                errors.append('El cónyuge seleccionado no existe.')
        if d['SpouseMemberId'] and d['SpouseExternalName']:
            errors.append('Seleccione un cónyuge miembro o escriba uno externo, no ambos.')
    else:
        d['SpouseMemberId'] = None
        d['SpouseExternalName'] = ''
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


def _render_form(title, m, db):
    duplicate = duplicate_info(find_duplicate(db, m.get('Cedula'), m.get('Id')))
    return render_template('member_form.html', title=title, m=m, duplicate=duplicate, ministries=_ministries(db),
                           spouse_members=db.execute('SELECT Id,MemberCode,FullName FROM Members WHERE DeletedAt IS NULL AND Id<>? ORDER BY FullName', (m.get('Id') or -1,)).fetchall(),
                           locked=not is_staff(), groups=GROUPS, statuses=STATUSES,
                           roles=CHURCH_ROLES, followups=FOLLOWUPS, marital_statuses=MARITAL_STATUSES, baptized_options=['', 'Sí', 'No'],
                           today=datetime.date.today().isoformat())


def suggest_split(full_name):
    """Registros antiguos solo tienen el nombre completo. Sugiere Nombres/Apellidos
    para no dejar el formulario vacío (la persona los revisa antes de guardar)."""
    parts = (full_name or '').split()
    if len(parts) <= 1:
        return (parts[0] if parts else ''), ''
    cut = 1 if len(parts) <= 3 else 2
    return ' '.join(parts[:cut]), ' '.join(parts[cut:])


def _row_values(d):
    full = f"{d['FirstName']} {d['LastName']}".strip()
    guardian = f"{d['GuardianFirstName']} {d['GuardianLastName']}".strip()
    return full, guardian


def next_member_code(db):
    """Devuelve el próximo código visible MEM-001 sin reutilizar códigos anteriores."""
    maximum = 0
    for row in db.execute("SELECT MemberCode FROM Members WHERE MemberCode IS NOT NULL AND trim(MemberCode)<>''").fetchall():
        code = row['MemberCode'] or ''
        if code.startswith('MEM-') and code[4:].isdigit():
            maximum = max(maximum, int(code[4:]))
    return f'MEM-{maximum + 1:03d}'


# ------------------------------------------------------------------ rutas
@bp.route('/members')
@login_required
def members():
    db = get_db()
    sc, params = scope()
    conds, params = [sc], list(params)
    mid = request.args.get('ministry', type=int)
    if is_staff() and mid:
        conds.append('MinistryId=?')
        params.append(mid)
    q = (request.args.get('q') or '').strip()
    rows = db.execute(
        f'SELECT Id,MemberCode,FirstName,LastName,{NAME_SQL} AS Name,Cedula,Phone,Status,JoinDate FROM Members'
        + where(*conds) + ' ORDER BY Name COLLATE NOCASE', params).fetchall()
    # Búsqueda tolerante a mayúsculas/minúsculas y acentos. Se filtra después
    # de aplicar el alcance de permisos para no exponer miembros no autorizados.
    if q:
        def fold(value):
            text = unicodedata.normalize('NFD', str(value or ''))
            return ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn').casefold().strip()
        needle = fold(q)
        rows = [r for r in rows if needle in fold(' '.join(str(r[k] or '') for k in ('MemberCode','Name','Cedula','Phone')))]
    return render_template('members.html', title='MIEMBROS', rows=rows, q=q)


@bp.route('/members/new', methods=['GET', 'POST'])
@login_required
def new_member():
    db = get_db()
    if request.method == 'POST':
        ids = {r['Id'] for r in _ministries(db)}
        d, errors = parse_form(request.form, ids)
        if d.get('SpouseMemberId'):
            other=db.execute('SELECT SpouseMemberId FROM Members WHERE Id=?',(d['SpouseMemberId'],)).fetchone()
            if other and other['SpouseMemberId']:
                errors.append('El cónyuge seleccionado ya tiene una relación matrimonial; revise su ficha.')
        if find_duplicate(db, d['Cedula']):
            errors.append('Ya existe un miembro registrado con esa cédula.')
        photo = None
        if not errors:
            photo, err = save_photo(request.files.get('ProfilePhoto'))
            if err:
                errors.append(err)
        if errors:
            for e in errors:
                flash(e)
            return _render_form('REGISTRO DE MIEMBRO', d, db)
        full, guardian = _row_values(d)
        cols = [c for c in FIELDS if c in d]
        try:
            cur = db.execute(
                f'INSERT INTO Members(FullName,GuardianName,ProfilePhoto,CreatedAt,{",".join(cols)}) '
                f'VALUES(?,?,?,?,{",".join("?" * len(cols))})',
                [full, guardian, photo, now_iso()] + [d[c] for c in cols])
            eid = cur.lastrowid
            member_code = next_member_code(db)
            db.execute('UPDATE Members SET MemberCode=? WHERE Id=?', (member_code, eid))
            if d.get('SpouseMemberId'):
                db.execute("UPDATE Members SET SpouseMemberId=?,SpouseExternalName='',MaritalStatus='Casado/a' WHERE Id=?", (eid,d['SpouseMemberId']))
            db.execute('INSERT INTO MemberHistory(MemberId,UserId,Action,Details,CreatedAt) VALUES(?,?,?,?,?)',
                       (eid, g.user['Id'], 'Crear', 'Registro inicial', now_iso()))
            audit('Crear', 'Miembro', eid, 'Nuevo miembro: ' + full)
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            delete_photo(photo)
            flash('Ya existe un miembro registrado con esa cédula.')
            return _render_form('REGISTRO DE MIEMBRO', d, db)
        flash('Miembro registrado correctamente.')
        return redirect(url_for('members.members'))
    return _render_form('REGISTRO DE MIEMBRO', {}, db)


@bp.route('/members/<int:i>/edit', methods=['GET', 'POST'])
@login_required
def edit_member(i):
    db = get_db()
    row = db.execute('SELECT * FROM Members WHERE Id=?', (i,)).fetchone()
    if not can_access_member(row):
        flash('No tiene permiso para este miembro.')
        return redirect(url_for('members.members'))
    if request.method == 'POST':
        ids = {r['Id'] for r in _ministries(db)}
        d, errors = parse_form(request.form, ids)
        if d.get('SpouseMemberId') == i:
            errors.append('Un miembro no puede ser su propio cónyuge.')
        if d.get('SpouseMemberId'):
            other=db.execute('SELECT SpouseMemberId FROM Members WHERE Id=?',(d['SpouseMemberId'],)).fetchone()
            if other and other['SpouseMemberId'] not in (None,i):
                errors.append('El cónyuge seleccionado ya tiene una relación matrimonial; revise su ficha.')
        if find_duplicate(db, d['Cedula'], i):
            errors.append('Ya existe otro miembro registrado con esa cédula.')
        new_photo = None
        if not errors:
            new_photo, err = save_photo(request.files.get('ProfilePhoto'))
            if err:
                errors.append(err)
        if errors:
            for e in errors:
                flash(e)
            merged = dict(row)
            merged.update(d)
            return _render_form('EDITAR MIEMBRO', merged, db)
        full, guardian = _row_values(d)
        photo = new_photo or row['ProfilePhoto']
        changed = [FIELDS[c] for c in d if c in FIELDS and (row[c] or None) != (d[c] or None)]
        if new_photo:
            changed.append('Foto de perfil')
        cols = [c for c in FIELDS if c in d]
        try:
            db.execute(
                f'UPDATE Members SET FullName=?,GuardianName=?,ProfilePhoto=?,{",".join(c + "=?" for c in cols)} WHERE Id=?',
                [full, guardian, photo] + [d[c] for c in cols] + [i])
            if row['SpouseMemberId'] and row['SpouseMemberId'] != d.get('SpouseMemberId'):
                db.execute('UPDATE Members SET SpouseMemberId=NULL WHERE Id=? AND SpouseMemberId=?', (row['SpouseMemberId'],i))
            if d.get('SpouseMemberId'):
                db.execute("UPDATE Members SET SpouseMemberId=?,SpouseExternalName='',MaritalStatus='Casado/a' WHERE Id=?", (i,d['SpouseMemberId']))
            detail = ('Cambios: ' + ', '.join(changed)) if changed else 'Ficha guardada sin cambios'
            db.execute('INSERT INTO MemberHistory(MemberId,UserId,Action,Details,CreatedAt) VALUES(?,?,?,?,?)',
                       (i, g.user['Id'], 'Editar', detail, now_iso()))
            audit('Editar', 'Miembro', i, f'Miembro actualizado: {full}. {detail}')
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            delete_photo(new_photo)
            flash('Ya existe otro miembro registrado con esa cédula.')
            merged = dict(row)
            merged.update(d)
            return _render_form('EDITAR MIEMBRO', merged, db)
        if new_photo:
            delete_photo(row['ProfilePhoto'])
        flash('Miembro actualizado correctamente.')
        return redirect(url_for('members.members'))
    m = dict(row)
    if not (m['FirstName'] or m['LastName']):
        m['FirstName'], m['LastName'] = suggest_split(m['FullName'])
        flash('Este registro es antiguo: se sugirieron Nombres y Apellidos a partir del nombre completo. Revíselos antes de guardar.')
    return _render_form('EDITAR MIEMBRO', m, db)


@bp.route('/members/<int:i>/delete', methods=['POST'])
@login_required
def del_member(i):
    db = get_db()
    row = db.execute(f'SELECT Id,MinistryId,ProfilePhoto,{NAME_SQL} AS Name FROM Members WHERE Id=?', (i,)).fetchone()
    if not can_access_member(row):
        flash('No tiene permiso para eliminar este miembro.')
        return redirect(url_for('members.members'))
    db.execute('UPDATE Members SET DeletedAt=? WHERE Id=?', (now_iso(), i))
    db.execute('INSERT INTO MemberHistory(MemberId,UserId,Action,Details,CreatedAt) VALUES(?,?,?,?,?)', (i,g.user['Id'],'Eliminar','Enviado a papelera',now_iso()))
    audit('Eliminar', 'Miembro', i, 'Miembro enviado a papelera')
    db.commit()
    flash('Miembro eliminado.')
    return redirect(url_for('members.members'))


@bp.route('/members/<int:i>')
@login_required
def member_profile(i):
    db = get_db()
    m = db.execute('SELECT mem.*,mi.Name AS MinistryName FROM Members mem '
                   'LEFT JOIN Ministries mi ON mi.Id=mem.MinistryId WHERE mem.Id=?', (i,)).fetchone()
    if not can_access_member(m):
        flash('No tiene permiso para este miembro.')
        return redirect(url_for('members.members'))
    history = db.execute('SELECT h.Action,h.Details,h.CreatedAt,u.Username FROM MemberHistory h LEFT JOIN Users u ON u.Id=h.UserId WHERE h.MemberId=? ORDER BY h.Id DESC',
                         (i,)).fetchall()
    name = f"{m['FirstName'] or ''} {m['LastName'] or ''}".strip() or m['FullName']
    spouse = db.execute('SELECT Id,FullName,MemberCode FROM Members WHERE Id=? AND DeletedAt IS NULL',(m['SpouseMemberId'],)).fetchone() if m['SpouseMemberId'] else None
    return render_template('member_profile.html', title='PERFIL DEL MIEMBRO', m=m, name=name, history=history, spouse=spouse, today=datetime.date.today().isoformat())


def find_duplicate(db, cedula, exclude=None):
    value = normalize_cedula(cedula)
    if not value:
        return None
    key = value.replace('-', '').replace(' ', '').replace('\t', '')
    return db.execute("SELECT * FROM Members WHERE replace(replace(replace(Cedula,'-',''),' ',''),char(9),'')=? AND Id<>? LIMIT 1", (key, exclude or -1)).fetchone()


def duplicate_info(row):
    if row is None:
        return {'exists': False}
    allowed = can_access_member(row)
    return {'exists': True, 'deleted': bool(row['DeletedAt']) if allowed else False,
            'url': url_for('members.member_profile', i=row['Id']) if allowed else None,
            'message': 'Esta cédula ya está registrada.' + (' Solicite acceso a Secretaría.' if not allowed else '')}


@bp.route('/api/members/check-cedula', methods=['POST'])
@login_required
def check_cedula():
    body = request.get_json(silent=True) or {}
    exclude = body.get('exclude')
    if exclude:
        try: exclude = int(exclude)
        except (TypeError, ValueError): return jsonify(error='Identificador no válido'), 400
        row = get_db().execute('SELECT * FROM Members WHERE Id=?', (str(exclude),)).fetchone()
        if not can_access_member(row):
            return jsonify(error='Sin permiso'), 403
    return jsonify(duplicate_info(find_duplicate(get_db(), str(body.get('cedula') or '')[:100], exclude)))


@bp.route('/trash')
@admin_required
def trash():
    rows = get_db().execute('SELECT * FROM Members WHERE DeletedAt IS NOT NULL ORDER BY DeletedAt DESC').fetchall()
    return render_template('trash.html', title='PAPELERA', rows=rows)


@bp.route('/trash/<int:i>/restore', methods=['POST'])
@admin_required
def restore(i):
    db = get_db()
    row = db.execute('SELECT * FROM Members WHERE Id=? AND DeletedAt IS NOT NULL', (i,)).fetchone()
    if row:
        if find_duplicate(db, row['Cedula'], i):
            flash('No se puede restaurar: otra ficha utiliza esa cédula. Corrija los registros existentes.')
        else:
            db.execute('UPDATE Members SET DeletedAt=NULL WHERE Id=?', (i,))
            db.execute('INSERT INTO MemberHistory(MemberId,UserId,Action,Details,CreatedAt) VALUES(?,?,?,?,?)', (i,g.user['Id'],'Restaurar','Restaurado desde papelera',now_iso()))
            audit('Restaurar', 'Miembro', i, 'Restaurado desde papelera')
            db.commit()
            flash('Miembro restaurado.')
    return redirect(url_for('members.trash'))

@bp.route('/members/import-trimestre', methods=['GET', 'POST'])
@admin_required
def import_trimestre_members():
    """Vista previa y confirmación manual; no modifica FOLIGRUC."""
    from .academic_members_import import preview, import_academic_names, IMPORT_KEY
    db=get_db()
    applied=bool(db.execute('SELECT ImportKey FROM ImportRuns WHERE ImportKey=?',(IMPORT_KEY,)).fetchone())
    if request.method=='POST':
        if applied:
            flash('Este listado ya fue importado; no se repetirá.', 'warning')
        else:
            try:
                stats=import_academic_names(current_app.config['DATABASE'],dry_run=False)
                flash(f"Importación completada: {stats['inserted']} miembros nuevos, {stats['duplicates']} existentes y {stats['ambiguous']} para revisión.", 'success')
            except Exception:
                current_app.logger.exception('Error importando trimestre')
                flash('No se pudo importar. No se guardaron cambios.', 'error')
        return redirect(url_for('members.import_trimestre_members'))
    entries=preview(db)
    return render_template('import_trimestre_members.html',title='IMPORTAR MIEMBROS',entries=entries,applied=applied)
