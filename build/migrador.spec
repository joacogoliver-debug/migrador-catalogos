# -*- mode: python ; coding: utf-8 -*-
"""
Receta de PyInstaller para el Migrador de Catálogos.

Se arma con:
    pyinstaller build/migrador.spec --noconfirm

Notas de por qué está así:

- `onefile`: un solo ejecutable es lo más simple de distribuir y de explicar
  ("bajá esto y hacé doble clic"). Arranca un poco más lento que `onedir`
  porque se descomprime en un temporal, pero para una app que después hace
  pedidos de red no se nota.

- `console=False`: sin ventana de terminal detrás, que es lo que hace que se
  sienta un programa y no un script. A cambio, un error de arranque sería
  invisible, así que el launcher escribe el traceback en
  ~/.migrador-catalogos/error.log.

- Los módulos del motor están en la raíz del repo, no dentro de app/, así que
  se agrega la raíz a `pathex`.

- `tiddl` y `yt_dlp` NO se incluyen a propósito. Son opcionales, pesan mucho, y
  el ejecutable público sale con el módulo de audio apagado. Quien los quiera
  los instala por su cuenta y corre desde el código fuente.
"""

import os

RAIZ = os.path.abspath(os.getcwd())
APP = os.path.join(RAIZ, "app")

# Si por algo faltara el .ico, se compila sin icono en vez de abortar el build.
_ico = os.path.join(APP, "web", "assets", "mojo-icon.ico")
ICONO = _ico if os.path.exists(_ico) else None

# Build CON el módulo de audio adentro: `python build/build.py --con-audio`.
# Es para uso propio, no para el binario que se publica. Suma unos 14 MB y mete
# un descargador de audio dentro del ejecutable, que es justo lo que el build
# público evita. Igual necesita ffmpeg en el PATH: es un binario del sistema y no
# se empaqueta.
CON_AUDIO = os.environ.get("MIGRADOR_BUILD_AUDIO", "") == "1"

# La variante con audio deja este archivo adentro del ejecutable. El servidor lo
# busca para prender el módulo sin depender de una variable de entorno: en un
# binario que se abre con doble clic no hay forma de pasarla, y pedirle al usuario
# que abra una consola para usar la funcion principal de su propio build no tiene
# sentido.
_MARCA_AUDIO = os.path.join(RAIZ, "build", "CON_AUDIO")
if CON_AUDIO and not os.path.exists(_MARCA_AUDIO):
    with open(_MARCA_AUDIO, "w", encoding="utf-8") as _f:
        _f.write("Este build incluye el modulo de audio (Tidal + referencia).")

# Estas se excluyen sólo en el build público.
DEPS_AUDIO = [
    "yt_dlp", "tiddl",
    "requests", "requests_cache", "urllib3", "websockets",
    "mutagen", "brotli", "curl_cffi", "Cryptodome", "secretstorage",
    "pydantic", "pydantic_core", "typer", "rich", "click",
]

a = Analysis(
    [os.path.join(APP, "launcher.py")],
    pathex=[RAIZ, APP],
    binaries=[],
    datas=[
        # La interfaz completa (html, css, js, tokens, assets).
        (os.path.join(APP, "web"), "web"),
    ] + ([(_MARCA_AUDIO, ".")] if CON_AUDIO else []),
    hiddenimports=[
        # Los importa el server por nombre y PyInstaller no siempre los ve.
        "server", "jobs",
        "relevar_core", "distributors", "productos", "portadas",
        "validar", "paquete", "migrar_core", "audio",
        # openpyxl carga sus writers de forma perezosa.
        "openpyxl.cell._writer",
        # pywebview NO se empaqueta: su backend de Windows va por pythonnet/.NET,
        # PyInstaller no logra llevarse el runtime y webview.start() se queda
        # colgado sin abrir ventana. La app usa el motor web de la máquina en modo
        # aplicación (ventana propia, sin barra ni pestañas), que no necesita
        # empaquetar nada.
    ],
    hookspath=[],
    runtime_hooks=[],
    # En el build público se excluye el módulo de audio y su árbol de
    # dependencias. Hay que hacerlo explícitamente: audio.verificar_entorno() hace
    # `import yt_dlp` para detectar si está, y a PyInstaller le alcanza ese import
    # para arrastrarlo con websockets, requests, mutagen, curl_cffi y compañía
    # (unos 14 MB, y un descargador de audio dentro del binario). Sacarlos no
    # rompe nada: el import está en try/except ImportError y el resultado es
    # "audio no disponible", que es el estado correcto ahí.
    excludes=[
        # Nada de esto se usa y sacarlo baja bastante el peso.
        "tkinter", "unittest", "pydoc", "doctest", "test",
        "numpy", "pandas", "matplotlib", "PIL",
        "streamlit", "fastapi", "uvicorn", "pytest",
        # Ver la nota de hiddenimports: no se usa y arrastra pythonnet.
        "webview", "clr", "clr_loader", "pythonnet",
    ] + ([] if CON_AUDIO else DEPS_AUDIO),
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Migrador de Catalogos" + (" (con audio)" if CON_AUDIO else ""),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX suele disparar falsos positivos de antivirus
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # .ico y no .png: en Windows PyInstaller sólo acepta exe/ico, y sin Pillow
    # instalado no convierte solo (compilaba en una máquina con Pillow y fallaba
    # en el CI, que no lo tiene). El .ico está commiteado para no depender de eso.
    icon=ICONO,
)
