"""Acceso a SQLite, esquema, migraciones y copias de seguridad.

Puntos clave:
* Una conexión por petición (flask.g), cerrada SIEMPRE al terminar. Si ocurre un
  error, lo pendiente se descarta (rollback) y la base nunca queda bloqueada.
* El esquema es idempotente: funciona con una base nueva y con las bases de
  versiones anteriores (agrega solo lo que falta, sin borrar datos).
* Las copias usan la API de respaldo de SQLite (consistente aunque haya
  escrituras en curso), no un simple copiado de archivo.
"""
import datetime
from zoneinfo import ZoneInfo
import glob
import os
import sqlite3
import time

from flask import current_app, g
from .passwords import hash_password

DEFAULT_ADMIN_PASSWORD = 'admin123'
AUTOBACKUP_NAME = 'iglesia_AUTOBACKUP.db'
BACKUP_PREFIXES = ('iglesia_backup_', 'iglesia_ANTES_RESTAURAR_')

SCHEMA = """
CREATE TABLE IF NOT EXISTS Ministries(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT UNIQUE NOT NULL,
    Leader TEXT, Assistant TEXT, Notes TEXT);

CREATE TABLE IF NOT EXISTS Members(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, FullName TEXT NOT NULL, Cedula TEXT UNIQUE,
    BirthDate TEXT, Address TEXT, Phone TEXT, JoinDate TEXT,
    Status TEXT NOT NULL DEFAULT 'Activo', MinistryId INTEGER, Notes TEXT,
    CreatedAt TEXT NOT NULL, GroupName TEXT NOT NULL DEFAULT '',
    GuardianName TEXT, GuardianPhone TEXT, FirstName TEXT, LastName TEXT,
    GuardianFirstName TEXT, GuardianLastName TEXT,
    FollowUpStatus TEXT DEFAULT 'Activo', FollowUpNotes TEXT, LastContact TEXT,
    ChurchRole TEXT DEFAULT 'Miembro', BaptismDate TEXT, ProfilePhoto TEXT,
    FOREIGN KEY(MinistryId) REFERENCES Ministries(Id));

CREATE TABLE IF NOT EXISTS MemberRegistration(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, FirstName TEXT NOT NULL, LastName TEXT NOT NULL,
    Cedula TEXT, BirthDate TEXT, Phone TEXT, Address TEXT, GroupName TEXT,
    GuardianPhone TEXT, CreatedAt TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS PublicRegistrationLinks(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, Token TEXT UNIQUE NOT NULL, ExpiresAt TEXT NOT NULL,
    Active INTEGER NOT NULL DEFAULT 1, CreatedAt TEXT NOT NULL, CreatedBy INTEGER);

CREATE TABLE IF NOT EXISTS Events(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT NOT NULL, EventDate TEXT, EventTime TEXT,
    Venue TEXT, MinistryId INTEGER, Responsible TEXT, Theme TEXT,
    Status TEXT NOT NULL DEFAULT 'Próximo', Notes TEXT, CreatedAt TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS Users(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, Username TEXT NOT NULL UNIQUE,
    PasswordHash TEXT NOT NULL, FullName TEXT, Role TEXT NOT NULL DEFAULT 'Ministry',
    MinistryId INTEGER, Active INTEGER NOT NULL DEFAULT 1, CreatedAt TEXT,
    LastLogin TEXT, FailedAttempts INTEGER DEFAULT 0, Locked INTEGER DEFAULT 0,
    FOREIGN KEY(MinistryId) REFERENCES Ministries(Id));

CREATE TABLE IF NOT EXISTS AuditLog(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, UserId INTEGER, Action TEXT, Entity TEXT,
    EntityId INTEGER, CreatedAt TEXT, Details TEXT,
    FOREIGN KEY(UserId) REFERENCES Users(Id));

CREATE TABLE IF NOT EXISTS LoginLog(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, Username TEXT, Success INTEGER,
    CreatedAt TEXT, IpAddress TEXT);

CREATE TABLE IF NOT EXISTS RateLimitLog(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, Scope TEXT NOT NULL, KeyValue TEXT NOT NULL,
    CreatedAt TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ImportRuns(
    ImportKey TEXT PRIMARY KEY, CreatedAt TEXT NOT NULL, Details TEXT);

CREATE TABLE IF NOT EXISTS Communities(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, MinistryId INTEGER NOT NULL, Name TEXT NOT NULL,
    LeaderMemberId INTEGER, AssistantMemberId INTEGER, Status TEXT NOT NULL DEFAULT 'Activa',
    CreatedDate TEXT, Notes TEXT, CreatedAt TEXT NOT NULL, UpdatedAt TEXT,
    FOREIGN KEY(MinistryId) REFERENCES Ministries(Id),
    FOREIGN KEY(LeaderMemberId) REFERENCES Members(Id),
    FOREIGN KEY(AssistantMemberId) REFERENCES Members(Id));

CREATE TABLE IF NOT EXISTS CommunityMembers(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, CommunityId INTEGER NOT NULL, MemberId INTEGER NOT NULL,
    Active INTEGER NOT NULL DEFAULT 1, JoinedAt TEXT NOT NULL, LeftAt TEXT,
    FOREIGN KEY(CommunityId) REFERENCES Communities(Id), FOREIGN KEY(MemberId) REFERENCES Members(Id));

CREATE TABLE IF NOT EXISTS MemberHistory(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, MemberId INTEGER, UserId INTEGER,
    Action TEXT, Details TEXT, CreatedAt TEXT);

CREATE TABLE IF NOT EXISTS Alerts(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, Title TEXT, Message TEXT, DueDate TEXT,
    Priority TEXT DEFAULT 'Normal', Active INTEGER DEFAULT 1, CreatedBy INTEGER,
    CreatedAt TEXT);

CREATE TABLE IF NOT EXISTS Conversions(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, MemberId INTEGER, FullName TEXT NOT NULL,
    ConversionDate TEXT NOT NULL, MinistryId INTEGER, RegisteredBy INTEGER,
    Notes TEXT, CreatedAt TEXT NOT NULL,
    FOREIGN KEY(MemberId) REFERENCES Members(Id),
    FOREIGN KEY(MinistryId) REFERENCES Ministries(Id),
    FOREIGN KEY(RegisteredBy) REFERENCES Users(Id));

CREATE TABLE IF NOT EXISTS Attendance(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, MemberId INTEGER NOT NULL, ServiceDate TEXT NOT NULL,
    ServiceKey TEXT NOT NULL, RegisteredAt TEXT NOT NULL, RegisteredBy INTEGER,
    FOREIGN KEY(MemberId) REFERENCES Members(Id), FOREIGN KEY(RegisteredBy) REFERENCES Users(Id));
CREATE TABLE IF NOT EXISTS AttendanceVisitors(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, FullName TEXT NOT NULL, Phone TEXT, ServiceDate TEXT NOT NULL,
    ServiceKey TEXT NOT NULL, RegisteredAt TEXT NOT NULL, RegisteredBy INTEGER,
    FOREIGN KEY(RegisteredBy) REFERENCES Users(Id));

CREATE TABLE IF NOT EXISTS NewBelieverLeaders(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, MemberId INTEGER NOT NULL UNIQUE, Active INTEGER NOT NULL DEFAULT 1, CreatedAt TEXT NOT NULL,
    FOREIGN KEY(MemberId) REFERENCES Members(Id));
CREATE TABLE IF NOT EXISTS ConversionFollowUps(
    Id INTEGER PRIMARY KEY AUTOINCREMENT, ConversionId INTEGER NOT NULL, ContactDate TEXT NOT NULL, ContactType TEXT NOT NULL,
    Status TEXT, Notes TEXT, RegisteredBy INTEGER, CreatedAt TEXT NOT NULL,
    FOREIGN KEY(ConversionId) REFERENCES Conversions(Id), FOREIGN KEY(RegisteredBy) REFERENCES Users(Id));
"""

# Columnas que pueden faltar en bases creadas por versiones anteriores.
LEGACY_COLUMNS = [
    ('Users', 'Active', 'INTEGER DEFAULT 1'),
    ('Users', 'FullName', 'TEXT'),
    ('Users', 'MinistryId', 'INTEGER'),
    ('Users', 'CreatedAt', 'TEXT'),
    ('Users', 'Permissions', "TEXT DEFAULT 'home,members,ministries,foligruc,birthdays,events'"),
    ('Members', 'DeletedAt', 'TEXT'),
    ('Members', 'MemberCode', 'TEXT'),
    ('Events', 'Reminder', 'TEXT'),
    ('Members', 'FirstName', 'TEXT'), ('Members', 'LastName', 'TEXT'),
    ('Members', 'GuardianFirstName', 'TEXT'), ('Members', 'GuardianLastName', 'TEXT'),
    ('Members', 'FollowUpStatus', "TEXT DEFAULT 'Activo'"),
    ('Members', 'FollowUpNotes', 'TEXT'), ('Members', 'LastContact', 'TEXT'),
    ('Members', 'ChurchRole', "TEXT DEFAULT 'Miembro'"),
    ('Members', 'BaptismDate', 'TEXT'), ('Members', 'Baptized', 'TEXT'), ('Members', 'ProfilePhoto', 'TEXT'), ('Members', 'MaritalStatus', 'TEXT'),
    ('Members', 'Profession', 'TEXT'),
    ('Members', 'FatherName', 'TEXT'), ('Members', 'MotherName', 'TEXT'),
    ('Members', 'TutorName', 'TEXT'), ('Members', 'SpouseMemberId', 'INTEGER'),
    ('Members', 'SpouseExternalName', 'TEXT'),
    ('Users', 'LastLogin', 'TEXT'), ('Users', 'FailedAttempts', 'INTEGER DEFAULT 0'),
    ('Users', 'Locked', 'INTEGER DEFAULT 0'),
    ('Users', 'SessionVersion', 'INTEGER DEFAULT 0'),
    ('AuditLog', 'Details', 'TEXT'),
    ('Ministries', 'LeaderMemberId', 'INTEGER'),
    ('Ministries', 'Assistant1MemberId', 'INTEGER'),
    ('Ministries', 'Assistant2MemberId', 'INTEGER'),
    ('Communities', 'Leader2MemberId', 'INTEGER'),
    ('Conversions', 'Phone', 'TEXT'),
    ('Conversions', 'Church', 'TEXT'),
    ('Conversions', 'LeaderId', 'INTEGER'),
]

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_members_ministry ON Members(MinistryId);
CREATE INDEX IF NOT EXISTS idx_members_status ON Members(Status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_members_membercode ON Members(MemberCode);
CREATE INDEX IF NOT EXISTS idx_events_date ON Events(EventDate);
CREATE INDEX IF NOT EXISTS idx_loginlog_user ON LoginLog(Username, CreatedAt);
CREATE INDEX IF NOT EXISTS idx_ratelimit_scope_key ON RateLimitLog(Scope, KeyValue, CreatedAt);
CREATE INDEX IF NOT EXISTS idx_audit_created ON AuditLog(CreatedAt);
CREATE INDEX IF NOT EXISTS idx_history_member ON MemberHistory(MemberId);
CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_unique ON Attendance(MemberId,ServiceDate,ServiceKey);
CREATE INDEX IF NOT EXISTS idx_attendance_service ON Attendance(ServiceDate,ServiceKey);
CREATE INDEX IF NOT EXISTS idx_attendance_visitors_service ON AttendanceVisitors(ServiceDate,ServiceKey);
CREATE INDEX IF NOT EXISTS idx_conversions_date ON Conversions(ConversionDate);
CREATE INDEX IF NOT EXISTS idx_conversions_ministry ON Conversions(MinistryId);
CREATE INDEX IF NOT EXISTS idx_communities_ministry ON Communities(MinistryId);
CREATE UNIQUE INDEX IF NOT EXISTS idx_communities_name_ministry ON Communities(MinistryId,Name);
CREATE INDEX IF NOT EXISTS idx_conversions_leader ON Conversions(LeaderId);
CREATE INDEX IF NOT EXISTS idx_followups_conversion ON ConversionFollowUps(ConversionId,ContactDate);
CREATE INDEX IF NOT EXISTS idx_communitymembers_community ON CommunityMembers(CommunityId,Active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_communitymembers_active_unique ON CommunityMembers(CommunityId,MemberId) WHERE Active=1;
"""


DR_TZ = ZoneInfo('America/Santo_Domingo')

def dr_now():
    return datetime.datetime.now(DR_TZ)

def today_dr():
    return dr_now().date()

def now_iso():
    # Hora oficial de República Dominicana (UTC-4).
    return dr_now().isoformat(timespec='seconds')


def connect(path):
    if str(path).startswith(('postgres://', 'postgresql://')):
        from .postgres import Connection
        return Connection(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def get_db():
    """Conexión de la petición actual (se crea una sola vez por petición)."""
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE'])
    return g.db


def close_db(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()  # lo no confirmado con commit() se descarta


def _ensure_col(conn, table, name, definition):
    if getattr(conn, 'is_postgres', False):
        conn.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {definition}')
        return
    cols = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
    if name not in cols:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')


def table_exists(conn, name):
    if getattr(conn, 'is_postgres', False):
        return conn.execute('SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?', (name.lower(),)).fetchone() is not None
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def init_db(path):
    """Crea o actualiza el esquema. Seguro de ejecutar en cada arranque."""
    conn = connect(path)
    try:
        if not getattr(conn, 'is_postgres', False) and table_exists(conn,'FStudents'):
            cols={r['name'] for r in conn.execute('PRAGMA table_info(FStudents)')}
            if 'FullName' not in cols:
                raise ValueError('FOLIGRUC anterior detectado. Ejecute manage.py convert-sqlite origen.db nuevo.db y use la copia convertida; vea README.')
        if getattr(conn, 'is_postgres', False):
            conn.execute('SELECT pg_advisory_xact_lock(271271)')
        from .foligruc import SCHEMA as ACADEMIC_SCHEMA, SUBJECTS
        conn.executescript(SCHEMA + ACADEMIC_SCHEMA + '''
        CREATE TABLE IF NOT EXISTS Photos(Id INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT UNIQUE NOT NULL, Data TEXT NOT NULL);
        ''')
        for table, name, definition in LEGACY_COLUMNS:
            _ensure_col(conn, table, name, definition)
        # V2.8: normaliza roles legados al código interno de Secretaria General.
        conn.execute("UPDATE Users SET Role='AssistantPastor' WHERE Role IN ('Secretaria','Secretary')")
        # Las columnas agregadas a cuentas antiguas pueden contener NULL.
        # Conservar bloqueos existentes y normalizar solo valores ausentes.
        conn.execute('UPDATE Users SET Locked=0 WHERE Locked IS NULL')
        conn.execute('UPDATE Users SET FailedAttempts=0 WHERE FailedAttempts IS NULL')
        conn.execute('UPDATE Users SET Active=1 WHERE Active IS NULL')
        conn.execute('UPDATE Users SET SessionVersion=0 WHERE SessionVersion IS NULL')
        # V2.9: código visible secuencial MEM-001, MEM-002...
        # Normaliza también los códigos V2.9 anteriores (MEM-000001) sin crear
        # ni duplicar miembros. Se usa un valor temporal para respetar el índice único.
        members_for_code = conn.execute("SELECT Id,MemberCode FROM Members ORDER BY Id").fetchall()
        for member in members_for_code:
            conn.execute('UPDATE Members SET MemberCode=? WHERE Id=?', (f'__MEM_TMP_{member["Id"]}__', member['Id']))
        for seq, member in enumerate(members_for_code, 1):
            conn.execute('UPDATE Members SET MemberCode=? WHERE Id=?', (f'MEM-{seq:03d}', member['Id']))
        conn.executescript(INDEXES)
        if getattr(conn, 'is_postgres', False):
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS members_cedula_normalized ON Members ((replace(replace(replace(Cedula,'-',''),' ',''),chr(9),''))) WHERE Cedula IS NOT NULL AND trim(Cedula)<>''")
        else:
            conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS cedula_unique_insert BEFORE INSERT ON Members
            WHEN NEW.Cedula IS NOT NULL AND trim(NEW.Cedula)<>''
            BEGIN
              SELECT RAISE(ABORT, 'cedula_duplicate') WHERE EXISTS (
                SELECT 1 FROM Members WHERE replace(replace(replace(Cedula,'-',''),' ',''),char(9),'') =
                replace(replace(replace(NEW.Cedula,'-',''),' ',''),char(9),''));
            END;
            CREATE TRIGGER IF NOT EXISTS cedula_unique_update BEFORE UPDATE OF Cedula ON Members
            WHEN NEW.Cedula IS NOT NULL AND trim(NEW.Cedula)<>''
            BEGIN
              SELECT RAISE(ABORT, 'cedula_duplicate') WHERE EXISTS (
                SELECT 1 FROM Members WHERE Id<>NEW.Id AND replace(replace(replace(Cedula,'-',''),' ',''),char(9),'') =
                replace(replace(replace(NEW.Cedula,'-',''),' ',''),char(9),''));
            END;
            """)
        if conn.execute('SELECT COUNT(*) FROM Users').fetchone()[0] == 0:
            conn.execute(
                'INSERT INTO Users(Username,PasswordHash,FullName,Role,Active,CreatedAt) '
                "VALUES('admin',?,'Administrador','Admin',1,?)",
                (hash_password(initial_password(path)), now_iso()))
        for n, name in enumerate(SUBJECTS, 1):
            conn.execute('INSERT INTO FSubjects(Id,Name,Teacher) VALUES(?,?,?) ON CONFLICT(Id) DO NOTHING', (n, name, ''))
        # V3.0.6.9: las 15 materias oficiales usan Id explícito (1..15).
        # PostgreSQL NO avanza la secuencia SERIAL con INSERT(Id,...).
        # Sin sincronizarla, el siguiente INSERT sin Id intenta reutilizar Id=1
        # y causa una violación de clave primaria (habitualmente error 409).
        if getattr(conn, 'is_postgres', False):
            conn.execute("SELECT setval(pg_get_serial_sequence('fsubjects', 'id'), GREATEST(COALESCE((SELECT MAX(Id) FROM FSubjects), 0), 1), (SELECT COUNT(*) > 0 FROM FSubjects))")
        conn.commit()
    finally:
        conn.close()


def audit(action, entity, entity_id=None, details='', uid=None):
    """Registra en el historial de actividad. Se confirma junto con el resto de
    la operación (mismo commit), así nunca queda un cambio sin su registro."""
    if uid is None and getattr(g, 'user', None) is not None:
        uid = g.user['Id']
    get_db().execute(
        'INSERT INTO AuditLog(UserId,Action,Entity,EntityId,CreatedAt,Details) VALUES(?,?,?,?,?,?)',
        (uid, action, entity, entity_id, now_iso(), details))


# --------------------------------------------------------------------------
# Copias de seguridad
# --------------------------------------------------------------------------
def backup_to(src_path, dest_path):
    """Copia consistente con la API de SQLite. Escribe a un temporal y luego
    reemplaza, de modo que una copia buena nunca se sustituye por una a medias."""
    tmp = dest_path + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, dest_path)


def is_healthy(path):
    try:
        conn = sqlite3.connect(path)
        try:
            return conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def is_backup_name(name):
    return (os.path.basename(name) == name and name.endswith('.db')
            and (name == AUTOBACKUP_NAME or name.startswith(BACKUP_PREFIXES)))


def list_backups(folder):
    items = []
    for fn in os.listdir(folder):
        if is_backup_name(fn):
            fp = os.path.join(folder, fn)
            items.append((fn, os.path.getmtime(fp)))
    items.sort(key=lambda x: x[1], reverse=True)
    return [(fn, datetime.datetime.fromtimestamp(mt).strftime('%d/%m/%Y %I:%M:%S %p'))
            for fn, mt in items]


def validate_backup_file(path):
    """Devuelve None si es una copia válida del sistema, o el motivo del rechazo."""
    try:
        with open(path, 'rb') as fh:
            if fh.read(16) != b'SQLite format 3\x00':
                return 'El archivo no es una base de datos SQLite.'
        conn = sqlite3.connect(path)
        try:
            ok = conn.execute('PRAGMA integrity_check').fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error:
        return 'El archivo está dañado o no es una base de datos válida.'
    if ok != 'ok' or 'Members' not in tables:
        return 'La copia seleccionada no parece ser una base de datos válida del sistema.'
    return None


def restore_from(source_path, db_path, folder):
    """Restaura `source_path` sobre la base actual. Antes guarda el estado actual.
    Devuelve el nombre de la copia de seguridad previa."""
    before = 'iglesia_ANTES_RESTAURAR_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.db'
    backup_to(db_path, os.path.join(folder, before))
    src = sqlite3.connect(source_path)
    dst = sqlite3.connect(db_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    init_db(db_path)  # deja la copia restaurada al día con el esquema actual
    return before


def _prune_daily(folder, keep):
    files = sorted(glob.glob(os.path.join(folder, 'iglesia_backup_diario_*.db')))
    for old in files[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass


def run_autobackup_once(app):
    db_path, folder = app.config['DATABASE'], app.config['BACKUP_DIR']
    if not os.path.exists(db_path):
        return
    if not is_healthy(db_path):
        # No se pisa la última copia buena con una base dañada.
        app.logger.error('La base de datos falla la verificación; se conserva la copia anterior.')
        return
    backup_to(db_path, os.path.join(folder, AUTOBACKUP_NAME))
    daily = os.path.join(folder, 'iglesia_backup_diario_' + datetime.date.today().strftime('%Y%m%d') + '.db')
    if not os.path.exists(daily):
        backup_to(db_path, daily)
        _prune_daily(folder, app.config['DAILY_BACKUPS_KEEP'])


def start_autobackup(app, interval=300):
    """Hilo en segundo plano: copia cada 5 min + una copia diaria (se guardan 14)."""
    import threading

    def loop():
        while True:
            try:
                run_autobackup_once(app)
            except Exception:  # noqa: BLE001 - el hilo nunca debe morir
                app.logger.exception('Falló la copia automática')
            time.sleep(interval)

    t = threading.Thread(target=loop, name='autobackup', daemon=True)
    t.start()
    return t


def initial_password(path):
    password = os.environ.get('ADMIN_INITIAL_PASSWORD')
    if password and len(password) >= 8:
        return password
    if str(path).startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('Base nueva: configure ADMIN_INITIAL_PASSWORD con al menos 8 caracteres.')
    return DEFAULT_ADMIN_PASSWORD


def import_legacy_photos(database, directory):
    import base64
    from io import BytesIO
    from PIL import Image, ImageOps
    conn = connect(database)
    try:
        for row in conn.execute("SELECT ProfilePhoto FROM Members WHERE ProfilePhoto IS NOT NULL").fetchall():
            name = row['ProfilePhoto']
            if os.path.basename(name) != name or conn.execute('SELECT Id FROM Photos WHERE Name=?',(name,)).fetchone():
                continue
            path = os.path.join(directory,name)
            if not os.path.isfile(path): continue
            with Image.open(path) as image:
                image=ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'),(600,600))
                out=BytesIO();image.save(out,format='JPEG',quality=85,optimize=True)
            conn.execute('INSERT INTO Photos(Name,Data) VALUES(?,?)',(name,base64.b64encode(out.getvalue()).decode('ascii')))
        conn.commit()
    finally:
        conn.close()
