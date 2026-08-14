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

a = Analysis(
    [os.path.join(APP, "launcher.py")],
    pathex=[RAIZ, APP],
    binaries=[],
    datas=[
        # La interfaz completa (html, css, js, tokens, assets).
        (os.path.join(APP, "web"), "web"),
    ],
    hiddenimports=[
        # Los importa el server por nombre y PyInstaller no siempre los ve.
        "server", "jobs",
        "relevar_core", "distributors", "productos", "portadas",
        "validar", "paquete", "migrar_core", "audio",
        # openpyxl carga sus writers de forma perezosa.
        "openpyxl.cell._writer",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Nada de esto se usa y sacarlo baja bastante el peso.
        "tkinter", "unittest", "pydoc", "doctest", "test",
        "numpy", "pandas", "matplotlib", "PIL",
        "streamlit", "fastapi", "uvicorn", "pytest",

        # El módulo de audio y TODO su árbol de dependencias.
        #
        # Hay que excluirlos explícitamente: audio.verificar_entorno() hace
        # `import yt_dlp` para detectar si está disponible, y a PyInstaller le
        # alcanza ese import para arrastrarlo con websockets, requests, urllib3,
        # mutagen, curl_cffi y compañía — unos 16 MB, y un descargador de audio
        # dentro del binario público, que es justo lo que queremos evitar.
        #
        # Sacarlos no rompe nada: el import está en un try/except ImportError y
        # el resultado es "audio no disponible", que es el estado correcto para
        # el ejecutable público.
        "yt_dlp", "tiddl",
        "requests", "requests_cache", "urllib3", "websockets",
        "mutagen", "brotli", "curl_cffi", "Cryptodome", "secretstorage",
        "pydantic", "pydantic_core", "typer", "rich", "click",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Migrador de Catalogos",
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
    icon=os.path.join(APP, "web", "assets", "mojo-icon.png"),
)
