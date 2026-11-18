@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo  WMS Data Copilot - inicio rapido
echo ==========================================

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creando entorno virtual e instalando dependencias...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :error
  ".venv\Scripts\python.exe" -m pip install --quiet -e ".[dev]" || goto :error
) else (
  echo [1/3] Entorno virtual listo.
)

if not exist "data\wms.db" (
  echo [2/3] Generando datos sinteticos, cargando base e indexando documentacion...
  ".venv\Scripts\python.exe" -m wms_copilot.cli bootstrap || goto :error
) else (
  echo [2/3] Base de datos y base de conocimiento listas.
)

echo [3/3] Iniciando la API en http://127.0.0.1:8000 ...
start "WMS Data Copilot - API" cmd /k ".venv\Scripts\python.exe -m wms_copilot.cli serve"
ping -n 6 127.0.0.1 >nul
start "" http://127.0.0.1:8000

echo.
echo Listo. Se abrio el navegador en http://127.0.0.1:8000
echo Para detener el copiloto, cierra la ventana "WMS Data Copilot - API".
echo.
ping -n 9 127.0.0.1 >nul
goto :eof

:error
echo.
echo Algo fallo. Revisa el mensaje de arriba.
pause
