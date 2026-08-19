@echo off
REM Abre el Migrador con el modulo de audio ACTIVADO (Tidal + referencia YouTube).
REM
REM Necesita, ademas de Python:
REM   pip install -r requirements-audio.txt
REM   ffmpeg en el PATH  ->  winget install --id Gyan.FFmpeg -e
REM
REM Si falta algo, la app lo detecta y no ofrece la opcion de audio.

cd /d "%~dp0"
set MIGRADOR_AUDIO=1

where python >nul 2>nul
if errorlevel 1 (
  echo No encontre Python. Instalalo desde https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import openpyxl" >nul 2>nul
if errorlevel 1 python -m pip install -r requirements-app.txt

python -c "import tiddl, yt_dlp" >nul 2>nul
if errorlevel 1 (
  echo Instalando las dependencias de audio por primera vez...
  python -m pip install -r requirements-audio.txt
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo ATENCION: falta ffmpeg en el PATH, y es necesario para extraer el FLAC.
  echo Instalalo con:  winget install --id Gyan.FFmpeg -e
  echo Despues cerra y volve a abrir esta ventana.
  echo.
  pause
)

echo Abriendo el Migrador CON audio...
python app\launcher.py
if errorlevel 1 pause
