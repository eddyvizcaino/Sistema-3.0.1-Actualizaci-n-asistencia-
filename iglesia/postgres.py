"""Adaptador DB-API para las consultas parametrizadas del proyecto heredado."""
import re
import sqlite3
from pathlib import Path
import psycopg

# PostgreSQL normaliza identificadores; conservamos las claves históricas de las vistas.
NAMES = {}
for source in Path(__file__).parent.glob('*.py'):
    for name in re.findall(r'\b[A-Z][A-Za-z0-9]*\b', source.read_text()):
        NAMES.setdefault(name.lower(), name)
NAMES.update({'name':'Name','id':'Id','data':'Data','total':'Total','ministry':'Ministry','ministryname':'MinistryName'})

class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class Cursor:
    def __init__(self, cursor, inserted=False):
        self.cursor = cursor
        self.lastrowid = cursor.fetchone()[0] if inserted else None
        self.rowcount = cursor.rowcount
    def _row(self, values):
        if values is None: return None
        return Row((NAMES.get(c.name.lower(), c.name), v) for c,v in zip(self.cursor.description, values))
    def fetchone(self): return self._row(self.cursor.fetchone())
    def fetchall(self): return [self._row(v) for v in self.cursor.fetchall()]
    def __iter__(self): return iter(self.fetchall())

class Connection:
    is_postgres = True
    def __init__(self, url):
        self.raw = psycopg.connect(url, connect_timeout=15, prepare_threshold=None)
        self.raw.execute("SET TIME ZONE 'America/Santo_Domingo'")
    def execute(self, sql, params=()):
        sql = sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT','SERIAL PRIMARY KEY')
        sql = sql.replace(' COLLATE NOCASE','').replace('char(9)','chr(9)')
        sql = sql.replace("date('now','localtime')", 'CURRENT_DATE')
        sql = sql.replace("strftime('%Y-%m','now','localtime')", "to_char(CURRENT_DATE,'YYYY-MM')")
        sql = sql.replace("strftime('%m-%d','now','localtime')", "to_char(CURRENT_DATE,'MM-DD')")
        sql = re.sub(r'\bdate\(([A-Za-z_.]+)\)',r"CAST(NULLIF(\1,'') AS DATE)",sql)
        # All statements are application-owned. Split quoted literals before replacing placeholders.
        parts = re.split(r"('(?:''|[^'])*')", sql)
        sql = ''.join(part.replace('?', '%s') if i%2==0 else part for i,part in enumerate(parts))
        inserted = bool(re.match(r'\s*INSERT\s+INTO', sql, re.I)) and 'RETURNING' not in sql.upper()
        if inserted: sql = sql.rstrip().rstrip(';') + ' RETURNING Id'
        try:
            cur = self.raw.execute(sql, params or None)
            # ON CONFLICT DO NOTHING may return no row.
            if inserted:
                result = Cursor(cur)
                row = cur.fetchone()
                result.lastrowid = row[0] if row else None
                return result
            return Cursor(cur)
        except psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError('Restricción de integridad') from exc
    def executescript(self, sql):
        for stmt in sql.split(';'):
            if stmt.strip(): self.execute(stmt)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()
