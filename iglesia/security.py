"""Autenticación, permisos, protección CSRF y ayudas de validación."""
import datetime
import hmac
import re
import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

ROLE_ADMIN = 'Admin'
ROLE_PASTOR = 'Pastor'
ROLE_ASSISTANT_PASTOR = 'AssistantPastor'
ROLE_MINISTRY = 'Ministry'
ROLE_ATTENDANCE = 'Attendance'
MIN_PASSWORD_LEN = 8

# Expresión SQL para mostrar el nombre (compatible con miembros antiguos sin Nombres/Apellidos)
NAME_SQL = "coalesce(nullif(trim(coalesce(FirstName,'')||' '||coalesce(LastName,'')),''),FullName)"


# ---------------------------------------------------------------- permisos
def is_admin():
    return g.get('user') is not None and g.user['Role'] == ROLE_ADMIN

def is_pastoral():
    return g.get('user') is not None and g.user['Role'] in (ROLE_ADMIN, ROLE_PASTOR, ROLE_ASSISTANT_PASTOR)


def login_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if g.get('user') is None:
            return redirect(url_for('auth.login'))
        return f(*a, **k)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if g.get('user') is None:
            return redirect(url_for('auth.login'))
        if not is_admin():
            flash('Esta opción es solo para Administrador.')
            return redirect(url_for('pages.home'))
        return f(*a, **k)
    return wrapper




def pastoral_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if g.get('user') is None:
            return redirect(url_for('auth.login'))
        if not is_pastoral():
            abort(403)
        return f(*a, **k)
    return wrapper


def is_staff():
    # Personal pastoral ve y gestiona los datos de los módulos autorizados sin limitarse a un ministerio.
    return is_pastoral()


def scope(alias=''):
    """(condición_sql, parámetros) que limita a los datos del ministerio del usuario.
    El administrador ve todo. Si un usuario de ministerio no tiene ministerio
    asignado no ve nada (falla cerrado)."""
    if is_staff():
        return (alias + '.' if alias else '') + 'DeletedAt IS NULL', []
    col = (alias + '.' if alias else '') + 'MinistryId'
    return f'{col}=? AND ' + (alias + '.' if alias else '') + 'DeletedAt IS NULL', [g.user['MinistryId']]


def event_scope(alias=''):
    """Como scope(), pero los eventos generales (sin ministerio) los ve todo el mundo."""
    if is_staff():
        return '', []
    col = (alias + '.' if alias else '') + 'MinistryId'
    mid = g.user['MinistryId']
    if mid is None:
        return f'{col} IS NULL', []
    return f'({col}=? OR {col} IS NULL)', [mid]


def can_edit_event(event_row):
    """Administrador: todos. Usuario de ministerio: solo los eventos de su ministerio
    (los generales son de solo lectura para él)."""
    if event_row is None:
        return False
    if is_staff():
        return True
    mid = g.user['MinistryId']
    return mid is not None and event_row['MinistryId'] == mid


def where(*conds):
    conds = [c for c in conds if c]
    return (' WHERE ' + ' AND '.join(conds)) if conds else ''


def can_access_member(member_row):
    if member_row is None:
        return False
    if is_staff():
        return True
    mid = g.user['MinistryId']
    return mid is not None and member_row['MinistryId'] == mid


# ------------------------------------------------------------------- CSRF
def csrf_token():
    token = session.get('_csrf')
    if not token:
        token = session['_csrf'] = secrets.token_urlsafe(32)
    return token


def check_csrf():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        sent = request.form.get('csrf_token') or request.headers.get('X-CSRFToken', '')
        expected = session.get('_csrf', '')
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400)


# ------------------------------------------------------------ validaciones
def clean(value, maxlen=200):
    """Texto recortado y con espacios normalizados en los extremos."""
    return (value or '').strip()[:maxlen]


def parse_date(value):
    """Devuelve (valor_iso_o_None, error_o_None) para un campo <input type=date>."""
    value = (value or '').strip()
    if not value:
        return None, None
    try:
        return datetime.date.fromisoformat(value).isoformat(), None
    except ValueError:
        return None, 'fecha no válida'


_CEDULA_DIGITS = re.compile(r'^\d{11}$')


def normalize_cedula(value):
    """001-0000000-1. Si trae 11 dígitos sin guiones se formatea; evita duplicados
    escondidos por el formato. Vacío -> None."""
    value = (value or '').strip()
    if not value:
        return None
    digits = re.sub(r'[\s-]', '', value)
    if _CEDULA_DIGITS.match(digits):
        return f'{digits[:3]}-{digits[3:10]}-{digits[10]}'
    return value[:30]


def password_problem(password, username=''):
    from .db import DEFAULT_ADMIN_PASSWORD
    password = password or ''
    if len(password) < MIN_PASSWORD_LEN:
        return f'Use al menos {MIN_PASSWORD_LEN} caracteres.'
    common = {'password','password123','12345678','123456789','qwerty123','admin123','administrador','iglesia123','clave1234'}
    if password.lower().replace(' ', '') in common:
        return 'Esa contraseña es demasiado común; elija una diferente.'
    if not re.search(r'[A-Za-zÁÉÍÓÚáéíóúÑñ]', password) or not re.search(r'\d', password):
        return 'La contraseña debe combinar letras y números.'
    if password == DEFAULT_ADMIN_PASSWORD:
        return 'Esa contraseña es la contraseña inicial conocida; elija otra.'
    if username and password.lower() == username.lower():
        return 'La contraseña no puede ser igual al nombre de usuario.'
    return None


def excel_safe(value):
    """Evita que Excel interprete texto como fórmula (=, +, -, @)."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


MODULES = ('home', 'members', 'ministries', 'communities', 'conversions', 'attendance', 'foligruc', 'birthdays', 'reports')
PASTORAL_MODULES = ('members', 'ministries', 'communities', 'conversions', 'attendance', 'foligruc', 'birthdays', 'reports')

def has_module(name):
    if is_admin():
        return True
    if not g.get('user'):
        return False
    role = g.user['Role']
    if role == ROLE_ATTENDANCE:
        return name == 'attendance'
    if role in (ROLE_PASTOR, ROLE_ASSISTANT_PASTOR):
        return name in PASTORAL_MODULES
    if role == ROLE_MINISTRY:
        
        if name == 'conversions' and g.user['MinistryId'] is not None:
            from .db import get_db
            row=get_db().execute('SELECT Name FROM Ministries WHERE Id=?',(g.user['MinistryId'],)).fetchone()
            n=(row['Name'] if row else '').lower()
            return 'nuevo' in n and 'creyente' in n
        return name in ('members', 'ministries', 'communities') and g.user['MinistryId'] is not None
    return False

def enforce_modules():
    if not g.get('user') or is_admin():
        return
    ep = request.endpoint or ''
    if ep.startswith('auth.') or ep in ('static', 'healthz'):
        return
    role = g.user['Role']
    if role == ROLE_ATTENDANCE:
        if ep.startswith('attendance.'):
            return
        abort(403)
    if role == ROLE_MINISTRY:
        if g.user['MinistryId'] is None:
            abort(403)
        if ep == 'pages.home':
            return redirect(url_for('ministries.ministries'))
        if ep.startswith('conversions.'):
            from .db import get_db
            row=get_db().execute('SELECT Name FROM Ministries WHERE Id=?',(g.user['MinistryId'],)).fetchone()
            n=(row['Name'] if row else '').lower()
            if 'nuevo' in n and 'creyente' in n:
                return
            abort(403)
        if ep.startswith('ministries.') or ep.startswith('members.') or ep.startswith('communities.'):
            if ep in ('members.trash','members.restore','members.registrations','members.registration_qr'):
                abort(403)
            return
        abort(403)
    if role in (ROLE_PASTOR, ROLE_ASSISTANT_PASTOR):
        if ep == 'pages.home':
            return redirect(url_for('members.members'))
        mapping={'pages.birthdays':'birthdays'}
        module=mapping.get(ep, ep.split('.')[0])
        if module not in PASTORAL_MODULES:
            abort(403)
        return
    abort(403)
