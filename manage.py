"""Mantenimiento sin servidor. Nunca imprime DATABASE_URL ni contraseñas."""
import argparse
import json
import os
from pathlib import Path
from iglesia.db import init_db, connect, import_legacy_photos
from iglesia.portable_backup import export_backup, restore_empty, TABLES


def main():
    parser=argparse.ArgumentParser(description='Administración Iglesia Web V2.9.7')
    parser.add_argument('--ask-database',action='store_true',help='Pedir URL PostgreSQL sin mostrarla ni guardarla')
    parser.add_argument('--ask-admin-password',action='store_true',help='Pedir contraseña inicial para una base nueva')
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('init')
    p=sub.add_parser('import-foligruc-nombres');p.add_argument('--apply',action='store_true',help='Aplicar importación (por defecto solo simulación)')
    p=sub.add_parser('backup');p.add_argument('file')
    p=sub.add_parser('restore-empty');p.add_argument('file')
    p=sub.add_parser('import-sqlite');p.add_argument('source');p.add_argument('--photos')
    p=sub.add_parser('convert-sqlite');p.add_argument('source');p.add_argument('output');p.add_argument('--photos')
    p=sub.add_parser('import-mayo-2026');p.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    from getpass import getpass
    if args.ask_database:
        os.environ['DATABASE_URL']=getpass('URL PostgreSQL (oculta): ')
    if args.ask_admin_password:
        os.environ['ADMIN_INITIAL_PASSWORD']=getpass('Contraseña inicial del administrador (mínimo 8): ')
    destination=os.environ.get('DATABASE_URL') or str(Path(__file__).parent/'iglesia.db')
    if args.action=='convert-sqlite':
        from iglesia.legacy_import import convert_sqlite
        target=Path(args.output)
        if target.exists():raise ValueError('El archivo de destino ya existe. Use un nombre nuevo.')
        content=convert_sqlite(args.source,args.photos)
        init_db(str(target));restore_empty(str(target),content)
        print('Conversión local completada. Origen intacto.');return
    if args.action=='init':init_db(destination);print('Esquema V2.9.7 listo.');return
    if args.action=='import-mayo-2026':
        from iglesia.may2026_import import import_may_2026
        st=import_may_2026(destination,args.dry_run)
        print(('SIMULACIÓN: ' if args.dry_run else '') + f"Fuente {st['source']}; agregados {st['inserted']}; duplicados omitidos {st['duplicates']}; ministerios creados {st['ministries_created']}")
        return
    if args.action=='import-foligruc-nombres':
        from iglesia.academic_members_import import import_academic_names
        st=import_academic_names(destination,dry_run=not args.apply)
        print(json.dumps(st,ensure_ascii=False));return
    if args.action=='backup':
        with open(args.file,'xb') as f:f.write(export_backup(destination))
        os.chmod(args.file,0o600);print('Respaldo creado.');return
    if args.action=='restore-empty':
        restore_empty(destination,Path(args.file).read_bytes());print('Respaldo restaurado en la base vacía.');return
    if args.action=='import-sqlite':
        from iglesia.legacy_import import convert_sqlite
        if not destination.startswith(('postgres://','postgresql://')):
            raise ValueError('Configure DATABASE_URL de PostgreSQL como destino.')
        content=convert_sqlite(args.source,args.photos)
        restore_empty(destination,content)
        print('Importación terminada; se conservó intacto el archivo de origen.')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Drivers can include connection details in errors. Do not print driver exceptions.
        if type(exc) is ValueError:print('No completado:',str(exc))
        else:print('No completado. Revise archivo, permisos, variables de entorno y conectividad. No se muestran credenciales.')
        raise SystemExit(1)
