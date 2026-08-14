@echo off
REM Abre el Migrador de Catalogos desde el codigo fuente (Windows).
REM Para el ejecutable ya compilado no hace falta esto: es doble clic.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo No encontre Python. Instalalo desde https://www.python.org/downloads/
  echo Acordate de tildar "Add Python to PATH" durante la instalacion.
  pause
  exit /b 1
)

python -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Instalando dependencias por primera vez...
  python -m pip install -r requirements-app.txt
)

echo Abriendo el Migrador de Catalogos...
python app\launcher.py
if errorlevel 1 pause
