"""
Arma el ejecutable de la app.

    python build/build.py

Chequea el entorno, corre PyInstaller con build/migrador.spec y deja el
resultado en dist/ junto a su SHA256, que es lo que se publica para que
cualquiera pueda verificar que el binario corresponde al código.

No firma el ejecutable: firmar cuesta plata (certificado EV en Windows, cuenta
de desarrollador en Apple) y este proyecto es gratis. En su lugar la confianza
se apoya en que el código es público, el binario se compila en GitHub Actions a
la vista de todos, y se publica el hash. Es la misma postura que yt-dlp.
"""

import hashlib
import os
import shutil
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(RAIZ, "build", "migrador.spec")
DIST = os.path.join(RAIZ, "dist")


def paso(texto):
    print(f"\n>>> {texto}")


def revisar_entorno():
    paso("Revisando el entorno")
    if sys.version_info < (3, 9):
        sys.exit("Hace falta Python 3.9 o más nuevo.")
    print(f"    Python {sys.version.split()[0]} en {sys.platform}")

    try:
        import PyInstaller  # noqa: F401
        print("    PyInstaller: ok")
    except ImportError:
        sys.exit("Falta PyInstaller. Instalalo con:  pip install pyinstaller")

    try:
        import openpyxl  # noqa: F401
        print("    openpyxl: ok")
    except ImportError:
        sys.exit("Falta openpyxl. Instalalo con:  pip install -r requirements.txt")

    faltan = [f for f in ("app/launcher.py", "app/server.py", "app/web/index.html",
                          "relevar_core.py", "validar.py", "paquete.py")
              if not os.path.exists(os.path.join(RAIZ, f))]
    if faltan:
        sys.exit(f"No encuentro estos archivos (¿estás corriendo desde la raíz del repo?): {faltan}")
    print("    archivos del proyecto: ok")


def probar_tests():
    """Corre la batería offline antes de empaquetar: no tiene sentido publicar un
    binario que no pasa sus propios tests."""
    paso("Corriendo los tests")
    tests = ["test_parse_description.py", "test_productos.py", "test_validar.py",
             "test_portadas.py", "test_paquete.py", "test_app.py",
             "test_migrar_core.py"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for t in tests:
        r = subprocess.run([sys.executable, t], cwd=RAIZ, env=env,
                           capture_output=True, text=True)
        estado = "ok" if r.returncode == 0 else "FALLÓ"
        print(f"    {t:28} {estado}")
        if r.returncode != 0:
            print(r.stdout[-2000:])
            print(r.stderr[-2000:])
            sys.exit("Los tests no pasaron: no empaqueto.")


def limpiar():
    paso("Limpiando builds anteriores")
    for d in (DIST, os.path.join(RAIZ, "build", "migrador")):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            print(f"    borré {os.path.relpath(d, RAIZ)}")


def empaquetar():
    paso("Empaquetando con PyInstaller (tarda unos minutos)")
    r = subprocess.run(
        [sys.executable, "-m", "PyInstaller", SPEC, "--noconfirm", "--clean",
         "--distpath", DIST, "--workpath", os.path.join(RAIZ, "build", "migrador")],
        cwd=RAIZ)
    if r.returncode != 0:
        sys.exit("PyInstaller falló.")


def resumen():
    paso("Resultado")
    if not os.path.isdir(DIST):
        sys.exit("No se generó dist/.")
    for nombre in sorted(os.listdir(DIST)):
        ruta = os.path.join(DIST, nombre)
        if not os.path.isfile(ruta):
            continue
        h = hashlib.sha256()
        with open(ruta, "rb") as f:
            for bloque in iter(lambda: f.read(1024 * 1024), b""):
                h.update(bloque)
        tam = os.path.getsize(ruta) / 1e6
        print(f"    {nombre}  ({tam:.1f} MB)")
        print(f"    SHA256: {h.hexdigest()}")
        with open(ruta + ".sha256", "w", encoding="utf-8") as f:
            f.write(f"{h.hexdigest()}  {nombre}\n")

    print("\n    Listo. El ejecutable está en dist/.")
    print("    En Windows y macOS va a mostrar un aviso de 'programa no reconocido'")
    print("    porque no está firmado: es esperable, y el README explica cómo seguir.")


if __name__ == "__main__":
    if "--con-audio" in sys.argv:
        # El spec lo lee de acá. Es para uso propio: mete tiddl/yt-dlp dentro del
        # ejecutable, algo que el build publico evita a proposito.
        os.environ["MIGRADOR_BUILD_AUDIO"] = "1"
        print(">>> Variante CON audio (uso propio, no para publicar)")
    revisar_entorno()
    if "--sin-tests" not in sys.argv:
        probar_tests()
    limpiar()
    empaquetar()
    resumen()
