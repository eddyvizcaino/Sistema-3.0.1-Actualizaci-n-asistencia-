"""Sistema de Iglesia Web - fábrica de la aplicación."""
import logging
import os
import secrets
import time
from logging.handlers import RotatingFileHandler

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db as dbmod
from .security import check_csrf, csrf_token, is_admin, is_pastoral

VERSION = 'V3.1.0'
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_TIMEOUT_SECONDS = 1800  # 30 min de inactividad
SESSION_MAX_SECONDS = 8 * 60 * 60  # máximo absoluto: 8 horas

# Toda la aplicación usa la hora oficial de República Dominicana.
os.environ['TZ'] = 'America/Santo_Domingo'
if hasattr(time, 'tzset'):
    time.tzset()


def _load_secret_key():
    """Clave de sesión: variable SECRET_KEY o, si no existe, una clave aleatoria
    guardada en secret.key (así las sesiones sobreviven a un reinicio y no hay
    una clave conocida escrita en el código)."""
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    path = os.path.join(BASE, 'secret.key')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(key)
    except OSError:
        pass  # sin permisos de escritura: la clave vive solo mientras corre el programa
    return key


def create_app(test_config=None):
    app = Flask(__name__, static_folder=os.path.join(BASE, 'static'),
                template_folder=os.path.join(BASE, 'templates'))
    # Render/Hostinger terminan TLS delante de Flask. ProxyFix permite reconocer
    # correctamente HTTPS sin confiar en cabeceras arbitrarias fuera del proxy.
    if os.environ.get('RENDER') or os.environ.get('HOSTINGER') or os.environ.get('PRODUCTION'):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        VERSION=VERSION,
        DATABASE=os.environ.get('DATABASE_URL') or os.path.join(BASE, 'iglesia.db'),
        BACKUP_DIR=BASE,
        UPLOAD_DIR=os.path.join(BASE, 'static', 'profiles'),
        DAILY_BACKUPS_KEEP=14,
        SECRET_KEY=None,
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,  # tope general (permite subir una copia .db); las fotos se limitan a 5 MB
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('IGLESIA_HTTPS') == '1' or bool(os.environ.get('RENDER') or os.environ.get('HOSTINGER') or os.environ.get('PRODUCTION')),
        SESSION_COOKIE_NAME='iglesia_session',
    )
    if os.environ.get('DATABASE_URL') and not os.environ['DATABASE_URL'].startswith(('postgres://','postgresql://')):
        raise RuntimeError('DATABASE_URL debe ser una URL PostgreSQL válida.')
    production = bool(os.environ.get('RENDER') or os.environ.get('HOSTINGER') or os.environ.get('PRODUCTION'))
    if production and not os.environ.get('DATABASE_URL'):
        raise RuntimeError('En producción debe configurar DATABASE_URL de PostgreSQL; no se permite SQLite.')
    if production and not os.environ.get('SECRET_KEY'):
        raise RuntimeError('Configure SECRET_KEY en producción.')
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY']:
        app.config['SECRET_KEY'] = _load_secret_key()
    os.makedirs(app.config['UPLOAD_DIR'], exist_ok=True)
    os.makedirs(app.config['BACKUP_DIR'], exist_ok=True)

    _setup_logging(app)
    dbmod.init_db(app.config['DATABASE'])
    # V2.9.7: carga única e idempotente del listado aprobado de mayo 2026.
    # ImportRuns evita que un reinicio vuelva a crear miembros eliminados después.
    from .may2026_import import import_may_2026
    try:
        import_may_2026(app.config['DATABASE'])
    except Exception:
        # Una importación auxiliar nunca debe impedir que la aplicación arranque.
        app.logger.exception('No se pudo ejecutar la importación ACTUALIZACION_MAYO_2026; la aplicación continuará disponible.')
    dbmod.import_legacy_photos(app.config['DATABASE'], app.config['UPLOAD_DIR'])
    app.teardown_appcontext(dbmod.close_db)

    from . import admin, auth, members, ministries, pages, reports, foligruc, conversions, communities, attendance
    for module in (auth, pages, members, ministries, communities, attendance, reports, admin, foligruc, conversions):
        app.register_blueprint(module.bp)

    from .security import has_module
    app.jinja_env.globals['has_module'] = has_module
    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.route('/healthz')
    def healthz():
        dbmod.get_db().execute('SELECT 1').fetchone()
        return {'status': 'ok', 'version': VERSION}

    @app.context_processor
    def inject():
        
        labels={'Admin':'Administrador','Pastor':'Pastor/a','AssistantPastor':'Secretaria General','Ministry':'Líder Ministerio','Attendance':'Registro de Asistencia'}
        u=g.get('user')
        return {'user': u, 'is_admin': is_admin(), 'is_pastoral': is_pastoral(), 'role_label': labels.get(u['Role'],u['Role']) if u else '', 'version': app.config['VERSION']}

    @app.before_request
    def guard():
        """Sesión: identifica al usuario en cada petición (así un bloqueo o cambio de
        rol aplica de inmediato), cierra por inactividad y exige cambiar la clave inicial."""
        g.user = None
        if request.endpoint == 'static' and request.view_args.get('filename','').startswith('profiles/'):
            from flask import abort
            abort(404)
        if request.endpoint in (None, 'static', 'healthz'):
            return None
        # En producción toda navegación de la aplicación debe usar HTTPS.
        if production and not request.is_secure:
            secure_url = request.url.replace('http://', 'https://', 1)
            return redirect(secure_url, code=308)
        uid = session.get('uid')
        if uid:
            now = time.time()
            last = session.get('last_activity')
            login_time = session.get('login_time', last or now)
            if (last and now - last > SESSION_TIMEOUT_SECONDS) or now - login_time > SESSION_MAX_SECONDS:
                session.clear()
                flash('Sesión cerrada por seguridad. Inicie sesión nuevamente.')
                return redirect(url_for('auth.login'))
            user = dbmod.get_db().execute('SELECT * FROM Users WHERE Id=?', (uid,)).fetchone()
            if user is None or not user['Active'] or user['Locked']:
                session.clear()
                flash('Su cuenta no está disponible. Contacte al administrador.')
                return redirect(url_for('auth.login'))
            if session.get('sv', 0) != (user['SessionVersion'] or 0):
                session.clear()
                flash('Su sesión fue cerrada por un cambio de seguridad. Inicie sesión nuevamente.')
                return redirect(url_for('auth.login'))
            g.user = user
            session['last_activity'] = now
            if session.get('must_change') and request.endpoint not in ('auth.change_password', 'auth.logout'):
                flash('Por seguridad, cambie la contraseña inicial antes de continuar.')
                return redirect(url_for('auth.change_password'))
        from .security import enforce_modules
        enforce_modules()
        check_csrf()
        return None

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault('Permissions-Policy', 'camera=(self), microphone=(), geolocation=(), payment=(), usb=()')
        resp.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; upgrade-insecure-requests")
        if request.is_secure or os.environ.get('RENDER') or os.environ.get('IGLESIA_HTTPS') == '1':
            resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        if request.endpoint != 'static':
            resp.headers['Cache-Control'] = 'no-store'  # no dejar datos de miembros en caché del navegador
        return resp

    def error_page(code, title, message):
        return render_template('error.html', title=title, code=code, message=message), code

    import sqlite3
    @app.errorhandler(sqlite3.IntegrityError)
    def integrity_error(_e):
        dbmod.get_db().rollback()
        return error_page(409, 'REGISTRO EN CONFLICTO', 'Ya existe un registro equivalente o relacionado. Recargue y revise antes de guardar.')

    @app.errorhandler(400)
    def bad_request(_e):
        return error_page(400, 'SOLICITUD NO VÁLIDA',
                          'La sesión expiró o el formulario ya no es válido. Vuelva a cargar la página e intente de nuevo.')

    @app.errorhandler(403)
    def forbidden(_e):
        return error_page(403, 'ACCESO NO AUTORIZADO', 'Su usuario no tiene permiso para este módulo.')

    @app.errorhandler(404)
    def not_found(_e):
        return error_page(404, 'PÁGINA NO ENCONTRADA', 'La página que busca no existe.')

    @app.errorhandler(405)
    def not_allowed(_e):
        return error_page(405, 'ACCIÓN NO PERMITIDA', 'Esa acción no está permitida desde esta dirección.')

    @app.errorhandler(429)
    def too_many_requests(_e):
        return error_page(429, 'DEMASIADAS SOLICITUDES',
                          'Se recibieron demasiados intentos en poco tiempo. Espere unos minutos e intente nuevamente.')

    @app.errorhandler(413)
    def too_large(_e):
        return error_page(413, 'ARCHIVO DEMASIADO GRANDE', 'El archivo supera el tamaño máximo permitido.')

    @app.errorhandler(500)
    def server_error(_e):
        return error_page(500, 'ERROR DEL SISTEMA',
                          'Ocurrió un error inesperado. Sus datos no se perdieron. Si se repite, avise al administrador.')

    return app


def _setup_logging(app):
    if app.testing or app.config.get('TESTING'):
        return
    handler = RotatingFileHandler(os.path.join(BASE, 'iglesia.log'), maxBytes=1_000_000,
                                  backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
