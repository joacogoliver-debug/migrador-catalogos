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
import shutil
import subprocess
import sys
import threading

_AQUI = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_AQUI)
for _p in (_RAIZ, _AQUI):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import server as backend            # noqa: E402

TITULO = "Migrador de Catálogos · MOJO"



# ============================================================
# Ventana de la aplicación
# ============================================================

def _abrir_ventana_pywebview(url):
    """Ventana nativa con pywebview. Sólo con --ventana-nativa.

    No es el camino por defecto porque empaquetado NO funciona: el backend de
    Windows va por pythonnet/.NET, PyInstaller no logra llevarse el runtime y
    `webview.start()` se queda colgado sin abrir ventana y sin lanzar ninguna
    excepción. Eso es peor que fallar: bloquea el hilo principal y nunca se llega
    a la alternativa. Desde el código fuente sí funciona.
    """
    try:
        import webview
    except Exception:
        return False
    try:
        webview.create_window(TITULO, url, width=1180, height=860, min_size=(900, 640))
        webview.start()
        return True
    except Exception:
        return False


def _navegador_app(url):
    """Abre el navegador en "modo app": ventana propia, sin barra de direcciones
    ni pestañas. Se ve y se usa como un programa de escritorio.

    Es más robusto que empotrar un motor web propio: usa el Edge/Chrome que ya
    está en la máquina, no agrega nada al ejecutable y no depende de que
    PyInstaller logre empaquetar un runtime .NET. En Windows 11 Edge está siempre.
    """
    candidatos = []
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidatos = [
            os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidatos = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            ruta = shutil.which(n)
            if ruta:
                candidatos.append(ruta)

    for exe in candidatos:
        if not exe or not os.path.exists(exe):
            continue
        try:
            # Perfil aparte para que la ventana no herede pestañas ni sesión del
            # navegador del usuario, y quede como una app independiente.
            perfil = os.path.join(backend.dir_datos(), "ventana")
            subprocess.Popen(
                [exe, f"--app={url}", f"--user-data-dir={perfil}",
                 "--no-first-run", "--no-default-browser-check"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False


def _diagnostico(url):
    """Reporte de lo que la app puede hacer en esta máquina.

    Existe porque el ejecutable se compila sin consola: si algo no arranca, el
    usuario no ve ningún mensaje. Con `--diagnostico` queda un archivo que se
    puede mandar para saber qué falta.
    """
    import platform
    lineas = [
        f"{TITULO}  v{backend.VERSION}",
        f"fecha: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}",
        f"python: {sys.version.split()[0]}   plataforma: {platform.platform()}",
        f"empaquetado: {bool(getattr(sys, 'frozen', False))}",
        f"url local: {url}",
        f"clave de YouTube configurada: {bool(backend.leer_clave())}",
        f"modulo de audio: {backend.AUDIO_HABILITADO}",
        "",
        "entorno de audio:",
    ]
    import audio as audio_mod
    for k, v in audio_mod.verificar_entorno().items():
        lineas.append(f"  {k}: {v}")

    lineas += ["", "ventana:"]
    try:
        import webview  # noqa: F401
        lineas.append("  pywebview importa: si")
        try:
            import webview.guilib as g
            lineas.append(f"  backend gui: {getattr(g, 'guilib', None) or 'sin inicializar'}")
        except Exception as e:
            lineas.append(f"  backend gui: error ({e})")
    except Exception as e:
        lineas.append(f"  pywebview importa: no ({e})")

    ruta = os.path.join(backend.dir_datos(), "diagnostico.txt")
    with open(ruta, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lineas) + "\n")
    print("\n".join(lineas))
    print(f"\nGuardado en: {ruta}")
    return ruta


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
                    help="Abrir en el navegador normal, con pestañas y barra de direcciones.")
    ap.add_argument("--ventana-nativa", action="store_true", dest="ventana_nativa",
                    help="Usar pywebview. Sólo desde el código: empaquetado se cuelga.")
    ap.add_argument("--diagnostico", action="store_true",
                    help="Escribir un reporte de qué puede hacer la app y salir.")
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

    if args.diagnostico:
        _diagnostico(url)
        srv.shutdown()
        srv.server_close()
        return 0

    try:
        if args.no_abrir:
            print("Ctrl+C para cerrar.")
            hilo.join()
        elif args.navegador:
            import webbrowser
            webbrowser.open(url)
            hilo.join()
        elif args.ventana_nativa and _abrir_ventana_pywebview(url):
            # La ventana se cerró: la app termina con ella.
            pass
        elif _navegador_app(url):
            # Ventana propia, sin barra de direcciones ni pestañas: se ve y se usa
            # como un programa de escritorio. Usa el motor web que ya está en la
            # máquina, así que no hay nada que empaquetar ni que pueda colgarse.
            print("Abrí la app en su propia ventana. Cerrala para terminar.")
            hilo.join()
        else:
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
