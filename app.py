"""Sistema de Iglesia Web - punto de entrada.

    python app.py                      -> abre el sistema en http://127.0.0.1:5000
    gunicorn app:app                   -> servidor de producción (Linux)

Variables de entorno opcionales:
    PORT         puerto (por defecto 5000)
    HOST         dirección; por defecto 127.0.0.1 (solo esta computadora).
                 Use HOST=0.0.0.0 para permitir acceso desde otros equipos de la red.
    SECRET_KEY   clave de sesión (si no se define se genera y guarda en secret.key)
    IGLESIA_HTTPS=1   marcar la cookie de sesión como "solo HTTPS" (si hay HTTPS delante)
"""
import os
import threading
import webbrowser

from iglesia import create_app
from iglesia.db import start_autobackup

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Con PORT definido (servicios en la nube) se escucha en todas las interfaces.
    host = os.environ.get('HOST') or ('0.0.0.0' if 'PORT' in os.environ else '127.0.0.1')
    start_autobackup(app)
    if host in ('127.0.0.1', 'localhost'):
        threading.Timer(1.2, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    print(f'Sistema de Iglesia Web {app.config["VERSION"]} en http://{host}:{port}  (Ctrl+C para detener)')
    app.run(host=host, port=port, debug=False)
