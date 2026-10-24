"""Inicio/cierre de sesión y cambio de contraseña."""
import datetime
import time

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .db import DEFAULT_ADMIN_PASSWORD, audit, get_db, now_iso
from .security import login_required, password_problem
from .passwords import hash_password, verify_password, needs_rehash

bp = Blueprint('auth', __name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MAX_IP_FAILED_ATTEMPTS = 20
# Hash falso para que "usuario inexistente" tarde lo mismo que "contraseña incorrecta".
_DUMMY_HASH = hash_password('no-existe-seguro-2029')

RESULT_FAILED, RESULT_OK, RESULT_BLOCKED = 0, 1, 2


def _log_attempt(db, username, result):
    db.execute('INSERT INTO LoginLog(Username,Success,CreatedAt,IpAddress) VALUES(?,?,?,?)',
               (username, result, now_iso(), request.remote_addr))


def _too_many_failures(db, username):
    """Bloqueo temporal: 5 fallos seguidos en 15 min. Se reinicia con un acceso correcto.
    Es temporal (no permanente) para que un atacante no pueda dejar fuera al administrador."""
    since = (datetime.datetime.now() - datetime.timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec='seconds')
    last_ok = db.execute('SELECT MAX(CreatedAt) FROM LoginLog WHERE Username=? AND Success=1',
                         (username,)).fetchone()[0]
    if last_ok and last_ok > since:
        since = last_ok
    n = db.execute('SELECT COUNT(*) FROM LoginLog WHERE Username=? AND Success=0 AND CreatedAt>?',
                   (username, since)).fetchone()[0]
    if n >= MAX_FAILED_ATTEMPTS:
        return True
    # También limita ataques distribuidos contra muchos usuarios desde la misma IP.
    ip = (request.remote_addr or '')[:64]
    ip_n = db.execute('SELECT COUNT(*) FROM LoginLog WHERE IpAddress=? AND Success=0 AND CreatedAt>?',
                      (ip, since)).fetchone()[0]
    return ip_n >= MAX_IP_FAILED_ATTEMPTS


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()[:80]
        password = request.form.get('password', '')
        db = get_db()

        if _too_many_failures(db, username):
            _log_attempt(db, username, RESULT_BLOCKED)
            audit('Acceso sospechoso bloqueado', 'Seguridad', details=f'Usuario={username}; IP={(request.remote_addr or '')[:64]}')
            db.commit()
            flash(f'Demasiados intentos fallidos. Espere {LOCKOUT_MINUTES} minutos e intente de nuevo.')
            return render_template('login.html')

        u = db.execute('SELECT * FROM Users WHERE Username=? AND Active=1', (username,)).fetchone()
        password_ok = verify_password(u['PasswordHash'] if u else _DUMMY_HASH, password)
        if u and password_ok and not u['Locked']:
            db.execute('UPDATE Users SET LastLogin=?,FailedAttempts=0 WHERE Id=?', (now_iso(), u['Id']))
            # Migra transparentemente hashes Werkzeug antiguos a Argon2id tras un login correcto.
            if needs_rehash(u['PasswordHash']):
                db.execute('UPDATE Users SET PasswordHash=? WHERE Id=?', (hash_password(password), u['Id']))
            _log_attempt(db, u['Username'], RESULT_OK)
            audit('Iniciar sesión', 'Seguridad', uid=u['Id'])
            db.commit()
            session.clear()  # nueva sesión al iniciar (evita fijación de sesión)
            session['uid'] = u['Id']
            session['sv'] = u['SessionVersion'] or 0
            session['last_activity'] = time.time()
            session['login_time'] = time.time()
            if password == DEFAULT_ADMIN_PASSWORD:
                session['must_change'] = True
                flash('Está usando la contraseña inicial. Debe cambiarla para continuar.')
                return redirect(url_for('auth.change_password'))
            if u['Role'] == 'Attendance':
                return redirect(url_for('attendance.mobile_app'))
            if u['Role'] == 'Ministry' and u['MinistryId'] is not None:
                return redirect(url_for('ministries.ministries'))
            return redirect(url_for('pages.home'))

        _log_attempt(db, username, RESULT_FAILED)
        if u:
            db.execute('UPDATE Users SET FailedAttempts=coalesce(FailedAttempts,0)+1 WHERE Id=?', (u['Id'],))
        db.commit()
        flash('Usuario o contraseña incorrectos o cuenta bloqueada.')
    return render_template('login.html')


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        db = get_db()
        current = request.form.get('current', '')
        new = request.form.get('new', '')
        if not verify_password(g.user['PasswordHash'], current):
            flash('Contraseña actual incorrecta.')
        elif (problem := password_problem(new, g.user['Username'])):
            flash(problem)
        else:
            db.execute('UPDATE Users SET PasswordHash=?,SessionVersion=coalesce(SessionVersion,0)+1 WHERE Id=?', (hash_password(new), g.user['Id']))
            audit('Cambiar contraseña', 'Usuario', g.user['Id'], 'Contraseña actualizada; se renovará la sesión')
            db.commit()
            uid = g.user['Id']
            sv = (g.user['SessionVersion'] or 0) + 1
            session.clear()
            session['uid'] = uid
            session['sv'] = sv
            session['last_activity'] = time.time()
            session['login_time'] = time.time()
            flash('Contraseña actualizada. La sesión fue renovada por seguridad.')
            return redirect(url_for('pages.home'))
    return render_template('change_password.html')
