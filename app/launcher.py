"""
Punto de entrada de la app de escritorio.

Levanta el servidor local en un puerto libre y abre la interfaz. Si `pywebview`
está instalado, la abre en una ventana nativa y la app se siente como un
programa de escritorio; si no, cae en el navegador por defecto, que funciona
igual de bien y no obliga a instalar nada.

Se corre así:
    python app/launcher.py            # ventana nativa o navegador
    python app/launcher.py --puerto 8777 --no-abrir
"""

import argparse
import os
import sys
import threading

_AQUI = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_AQUI)
for _p in (_RAIZ, _AQUI):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import server as backend            # noqa: E402

TITULO = "Migrador de Catálogos · MOJO"


def _abrir_ventana(url):
    """Ventana nativa con pywebview. Devuelve False si no está disponible."""
    try:
        import webview
    except ImportError:
        return False

    # El servidor ya corre en su propio hilo; webview toma el hilo principal,
    # que es un requisito suyo en macOS.
    webview.create_window(TITULO, url, width=1180, height=860, min_size=(900, 640))
    webview.start()
    return True


def _registrar_falla(e):
    """Deja el error en un archivo. Empaquetado sin consola, una excepción al
    arrancar sería invisible: sin esto el usuario ve que 'no abre' y no hay
    forma de saber por qué."""
    import datetime
    import traceback
    try:
        ruta = os.path.join(backend.dir_datos(), "error.log")
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.datetime.now().isoformat()} =====\n")
            f.write(traceback.format_exc())
        print(f"El detalle quedó en: {ruta}")
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=TITULO)
    ap.add_argument("--puerto", type=int, default=0,
                    help="Puerto local. 0 = elegir uno libre (recomendado).")
    ap.add_argument("--no-abrir", action="store_true",
                    help="No abrir la interfaz; sólo dejar el servidor escuchando.")
    ap.add_argument("--navegador", action="store_true",
                    help="Forzar el navegador en vez de la ventana nativa.")
    args = ap.parse_args(argv)

    srv = backend.crear_servidor(args.puerto)
    puerto = srv.server_address[1]
    url = f"http://127.0.0.1:{puerto}"

    hilo = threading.Thread(target=srv.serve_forever, name="http", daemon=True)
    hilo.start()

    print(f"{TITULO}  v{backend.VERSION}")
    print(f"Escuchando en {url}")
    if not backend.leer_clave():
        print("Primera vez: la app te va a pedir la clave de la API de YouTube.")

    try:
        if args.no_abrir:
            print("Ctrl+C para cerrar.")
            hilo.join()
        elif args.navegador or not _abrir_ventana(url):
            import webbrowser
            webbrowser.open(url)
            print("Abrí la app en tu navegador. Ctrl+C acá para cerrarla.")
            hilo.join()
    except KeyboardInterrupt:
        print("\nCerrando…")
    finally:
        backend.ESTADO.limpiar()
        srv.shutdown()
        srv.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                       # noqa: BLE001
        print(f"No se pudo iniciar la app: {e}")
        _registrar_falla(e)
        sys.exit(1)
