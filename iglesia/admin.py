"""Funciones exclusivas del Administrador: usuarios, seguridad, historial y copias."""
import os
import sqlite3
import tempfile

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, send_file, session, url_for)

from . import db as dbmod
from .db import audit, get_db, now_iso
from .security import ROLE_ADMIN, ROLE_PASTOR, ROLE_ASSISTANT_PASTOR, ROLE_MINISTRY, ROLE_ATTENDANCE, admin_required, clean, password_problem
from .passwords import hash_password

bp = Blueprint('admin', __name__)


# ---------------------------------------------------------------- usuarios
@bp.route('/users', methods=['GET', 'POST'])
@admin_required
def users():
    db = get_db()
    mins = db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()
    form = {}
    if request.method == 'POST':
        form = request.form
        if form.get('action') == 'permissions':
            from .security import MODULES
            uid = form.get('uid', type=int)
            target = db.execute('SELECT * FROM Users WHERE Id=?',(uid,)).fetchone()
            if not target or target['Role']=='Admin':
                flash('Seleccione un usuario no administrador.')
            else:
                permissions=','.join(m for m in MODULES if m in form.getlist('Permissions'))
                db.execute('UPDATE Users SET Permissions=? WHERE Id=?',(permissions,uid))
                audit('Permisos','Usuario',uid,permissions);db.commit()
                flash('Permisos actualizados.')
            return redirect(url_for('admin.users'))
        username = clean(form.get('Username'), 60)
        password = form.get('Password', '')
        role = form.get('Role', ROLE_MINISTRY)
        raw_min = (form.get('MinistryId') or '').strip()
        ministry_id = int(raw_min) if raw_min.isdigit() else None
        problem = None
        if not username:
            problem = 'El usuario es obligatorio.'
        elif role not in (ROLE_ADMIN, ROLE_PASTOR, ROLE_ASSISTANT_PASTOR, ROLE_MINISTRY, ROLE_ATTENDANCE):
            problem = 'Tipo de usuario no válido.'
        elif role == ROLE_MINISTRY and ministry_id not in {m['Id'] for m in mins}:
            problem = 'Un usuario de ministerio debe tener un ministerio asignado.'
        else:
            problem = password_problem(password, username)
        if problem:
            flash(problem)
        else:
            try:
                cur = db.execute(
                    'INSERT INTO Users(Username,PasswordHash,FullName,Role,MinistryId,Active,CreatedAt) '
                    'VALUES(?,?,?,?,?,1,?)',
                    (username, hash_password(password), clean(form.get('FullName'), 120), role,
                     ministry_id if role == ROLE_MINISTRY else None, now_iso()))
                from .security import MODULES
                permissions = ','.join(m for m in MODULES if m in form.getlist('Permissions'))
                db.execute('UPDATE Users SET Permissions=? WHERE Id=?', (permissions, cur.lastrowid))
                audit('Crear', 'Usuario', cur.lastrowid, f'Usuario creado: {username} ({role})')
                db.commit()
                flash('Usuario creado correctamente.')
                return redirect(url_for('admin.users'))
            except sqlite3.IntegrityError:
                db.rollback()
                flash('Ese nombre de usuario ya existe.')
    rows = db.execute('SELECT u.Id,u.Username,u.FullName,u.Role,u.Permissions,m.Name AS Ministry FROM Users u '
                      'LEFT JOIN Ministries m ON m.Id=u.MinistryId ORDER BY u.Username').fetchall()
    return render_template('users.html', title='USUARIOS Y PERMISOS', rows=rows, mins=mins, form=form)


@bp.route('/users/<int:uid>/edit', methods=['GET', 'POST'])
@admin_required
def user_edit(uid):
    db = get_db()
    target = db.execute('SELECT * FROM Users WHERE Id=?', (uid,)).fetchone()
    if not target:
        flash('Usuario no encontrado.')
        return redirect(url_for('admin.users'))
    mins = db.execute("SELECT Id,Name FROM Ministries WHERE Name <> '__COMUNIDADES_ORACION__' ORDER BY Name").fetchall()
    if request.method == 'POST':
        username = clean(request.form.get('Username'), 60)
        full_name = clean(request.form.get('FullName'), 120)
        role = request.form.get('Role', ROLE_MINISTRY)
        raw_min = (request.form.get('MinistryId') or '').strip()
        ministry_id = int(raw_min) if raw_min.isdigit() else None
        active = 1 if request.form.get('Active') == '1' else 0
        problem = None
        if not username:
            problem = 'El usuario es obligatorio.'
        elif role not in (ROLE_ADMIN, ROLE_PASTOR, ROLE_ASSISTANT_PASTOR, ROLE_MINISTRY, ROLE_ATTENDANCE):
            problem = 'Tipo de usuario no válido.'
        elif role == ROLE_MINISTRY and ministry_id not in {m['Id'] for m in mins}:
            problem = 'Un usuario de ministerio debe tener un ministerio asignado.'
        elif uid == g.user['Id'] and (role != ROLE_ADMIN or not active):
            problem = 'No puede quitarse a sí mismo el rol de Administrador ni desactivar su propia cuenta.'
        if problem:
            flash(problem)
        else:
            from .security import MODULES
            permissions = ','.join(m for m in MODULES if m in request.form.getlist('Permissions'))
            password = request.form.get('Password', '')
            values = [username, full_name, role, ministry_id if role == ROLE_MINISTRY else None, active, permissions]
            sql = 'UPDATE Users SET Username=?,FullName=?,Role=?,MinistryId=?,Active=?,Permissions=?'
            if password:
                if (pp := password_problem(password, username)):
                    flash(pp)
                    return render_template('user_edit.html', title='EDITAR USUARIO', target=dict(target), mins=mins)
                sql += ',PasswordHash=?,SessionVersion=coalesce(SessionVersion,0)+1'
                values.append(hash_password(password))
            sql += ' WHERE Id=?'; values.append(uid)
            try:
                db.execute(sql, values)
                audit('Editar', 'Usuario', uid, f'Usuario actualizado: {username} ({role})')
                db.commit(); flash('Usuario actualizado correctamente.')
                return redirect(url_for('admin.users'))
            except sqlite3.IntegrityError:
                db.rollback(); flash('Ese nombre de usuario ya existe.')
        merged = dict(target); merged.update(request.form); merged['Id'] = uid
        return render_template('user_edit.html', title='EDITAR USUARIO', target=merged, mins=mins)
    return render_template('user_edit.html', title='EDITAR USUARIO', target=dict(target), mins=mins)


@bp.route('/users/<int:uid>/delete', methods=['POST'])
@admin_required
def user_delete(uid):
    db = get_db()
    target = db.execute('SELECT * FROM Users WHERE Id=?', (uid,)).fetchone()
    if not target:
        flash('Usuario no encontrado.')
    elif uid == g.user['Id']:
        flash('No puede eliminar su propia cuenta de Administrador.')
    elif target['Role'] == ROLE_ADMIN and db.execute("SELECT COUNT(*) FROM Users WHERE Role='Admin' AND Active=1").fetchone()[0] <= 1:
        flash('No se puede eliminar el último Administrador activo.')
    else:
        username = target['Username']
        # Conserva la integridad del historial: desasocia referencias antes de borrar la cuenta.
        db.execute('UPDATE AuditLog SET UserId=NULL WHERE UserId=?', (uid,))
        db.execute('UPDATE MemberHistory SET UserId=NULL WHERE UserId=?', (uid,))
        db.execute('DELETE FROM Users WHERE Id=?', (uid,))
        audit('Eliminar', 'Usuario', uid, f'Usuario eliminado: {username}')
        db.commit(); flash('Usuario eliminado correctamente.')
    return redirect(url_for('admin.users'))


# ---------------------------------------------------------------- seguridad
@bp.route('/security', methods=['GET', 'POST'])
@admin_required
def security():
    db = get_db()
    if request.method == 'POST':
        uid = request.form.get('uid', type=int)
        lock = 1 if request.form.get('locked') == '1' else 0
        target = db.execute('SELECT Id,Username FROM Users WHERE Id=?', (uid,)).fetchone()
        if not target:
            flash('Usuario no encontrado.')
        elif target['Id'] == g.user['Id'] and lock:
            flash('No puede bloquear su propia cuenta.')
        else:
            db.execute('UPDATE Users SET Locked=?,FailedAttempts=0 WHERE Id=?', (lock, uid))
            audit('Cambiar bloqueo', 'Usuario', uid, ('Bloqueado: ' if lock else 'Desbloqueado: ') + target['Username'])
            db.commit()
            flash('Cuenta bloqueada.' if lock else 'Cuenta desbloqueada.')
        return redirect(url_for('admin.security'))
    users_ = db.execute('SELECT Id,Username,FullName,LastLogin,FailedAttempts,Locked,Active FROM Users '
                        'ORDER BY Username').fetchall()
    logs = db.execute('SELECT Username,Success,CreatedAt,IpAddress FROM LoginLog ORDER BY Id DESC LIMIT 50').fetchall()
    return render_template('security.html', title='SEGURIDAD', users=users_, logs=logs)


# ---------------------------------------------------------------- historial
@bp.route('/audit')
@admin_required
def audit_view():
    user = request.args.get('user', '')
    day = request.args.get('day', '')
    db = get_db()
    q = ('SELECT a.CreatedAt,u.Username,a.Action,a.Entity,a.EntityId,a.Details FROM AuditLog a '
         'LEFT JOIN Users u ON u.Id=a.UserId WHERE 1=1')
    params = []
    if user:
        q += ' AND u.Username=?'
        params.append(user)
    if day:
        q += ' AND date(a.CreatedAt)=?'
        params.append(day)
    rows = db.execute(q + ' ORDER BY a.Id DESC LIMIT 1000', params).fetchall()
    users_ = db.execute('SELECT Username FROM Users ORDER BY Username').fetchall()
    return render_template('audit.html', title='HISTORIAL DE ACTIVIDAD', rows=rows, users=users_, user=user, day=day)


# ------------------------------------------------------------------- copias
def _backup_dir():
    return current_app.config['BACKUP_DIR']


@bp.route('/backup')
@admin_required
def backup():
    if getattr(get_db(), 'is_postgres', False):
        return render_template('backup_postgres.html', title='RESPALDO POSTGRESQL')
    auto = os.path.join(_backup_dir(), dbmod.AUTOBACKUP_NAME)
    last = None
    if os.path.exists(auto):
        import datetime
        last = datetime.datetime.fromtimestamp(os.path.getmtime(auto)).strftime('%d/%m/%Y %I:%M:%S %p')
    return render_template('backup.html', title='COPIA DE SEGURIDAD', last=last,
                           backups=dbmod.list_backups(_backup_dir()))


@bp.route('/backup/download', methods=['POST'])
@admin_required
def backup_download():
    if getattr(get_db(), 'is_postgres', False):
        from .portable_backup import export_backup
        from io import BytesIO
        dbmod.close_db()
        data = export_backup(current_app.config['DATABASE'])
        audit('Copia manual', 'PostgreSQL', details='Respaldo lógico completo')
        get_db().commit()
        return send_file(BytesIO(data), as_attachment=True, download_name='iglesia_V2_9_respaldo.json', mimetype='application/json')
    import datetime
    out = os.path.join(_backup_dir(), 'iglesia_backup_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.db')
    dbmod.backup_to(current_app.config['DATABASE'], out)
    audit('Copia manual', 'Copia de seguridad', details=os.path.basename(out))
    get_db().commit()
    return send_file(out, as_attachment=True, download_name=os.path.basename(out))


@bp.route('/backup/restore', methods=['POST'])
@admin_required
def backup_restore():
    if getattr(get_db(), 'is_postgres', False):
        upload = request.files.get('backup_file')
        if not upload or not upload.filename or not upload.filename.lower().endswith('.json'):
            flash('Seleccione un respaldo JSON válido.')
            return redirect(url_for('admin.backup'))
        if request.form.get('confirmation') != 'RESTAURAR':
            flash('Escriba RESTAURAR para confirmar el reemplazo de los datos actuales.')
            return redirect(url_for('admin.backup'))
        try:
            content = upload.read()
            if len(content) > 40 * 1024 * 1024:
                raise ValueError('El respaldo supera el límite de 40 MB.')
            from .portable_backup import restore_empty
            who = g.user['Username']
            dbmod.close_db()
            restore_empty(current_app.config['DATABASE'], content, replace_existing=True)
            conn = dbmod.connect(current_app.config['DATABASE'])
            try:
                conn.execute('INSERT INTO AuditLog(UserId,Action,Entity,CreatedAt,Details) VALUES(NULL,?,?,?,?)',
                             ('Restaurar', 'PostgreSQL', now_iso(), f'Restauración iniciada por {who}'))
                conn.commit()
            finally:
                conn.close()
            session.clear()
            flash('Base de datos restaurada. Inicie sesión con un usuario del respaldo.')
            return redirect(url_for('auth.login'))
        except (ValueError, UnicodeDecodeError) as exc:
            flash(str(exc))
            return redirect(url_for('admin.backup'))
        except Exception:
            current_app.logger.exception('Error al restaurar PostgreSQL')
            flash('La restauración falló. Revise los registros; la operación se revierte si falló antes de confirmarse.')
            return redirect(url_for('admin.backup'))
    saved = (request.form.get('saved_backup') or '').strip()
    upload = request.files.get('backup_file')
    temp = None
    try:
        if upload and upload.filename:
            if not upload.filename.lower().endswith('.db'):
                flash('El archivo seleccionado debe ser una base de datos .db.')
                return redirect(url_for('admin.backup'))
            fd, temp = tempfile.mkstemp(suffix='.db', dir=_backup_dir())
            os.close(fd)
            upload.save(temp)
            source = temp
        elif saved:
            if not dbmod.is_backup_name(saved):
                flash('Copia de seguridad no válida.')
                return redirect(url_for('admin.backup'))
            source = os.path.join(_backup_dir(), saved)
            if not os.path.isfile(source):
                flash('No se encontró la copia seleccionada.')
                return redirect(url_for('admin.backup'))
        else:
            flash('Seleccione una copia de seguridad para restaurar.')
            return redirect(url_for('admin.backup'))

        problem = dbmod.validate_backup_file(source)
        if problem:
            flash(problem)
            return redirect(url_for('admin.backup'))

        who = g.user['Username']
        dbmod.close_db()  # libera la conexión de esta petición antes de reemplazar la base
        before = dbmod.restore_from(source, current_app.config['DATABASE'], _backup_dir())
        # La base restaurada puede tener otros usuarios/roles: se cierra la sesión actual.
        # El registro se guarda sin usuario porque el id actual podría no existir en la copia.
        conn = dbmod.connect(current_app.config['DATABASE'])
        try:
            conn.execute('INSERT INTO AuditLog(UserId,Action,Entity,CreatedAt,Details) VALUES(NULL,?,?,?,?)',
                         ('Restaurar', 'Copia de seguridad', now_iso(),
                          f'Restaurada por {who}. Estado anterior guardado en {before}'))
            conn.commit()
        finally:
            conn.close()
        session.clear()
        flash('Copia restaurada correctamente. Se guardó el estado anterior en: ' + before +
              '. Inicie sesión nuevamente.')
        return redirect(url_for('auth.login'))
    except Exception:  # noqa: BLE001
        current_app.logger.exception('Error al restaurar copia')
        flash('No se pudo restaurar la copia. La base actual no fue modificada si el error ocurrió antes de restaurar.')
        return redirect(url_for('admin.backup'))
    finally:
        if temp and os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
