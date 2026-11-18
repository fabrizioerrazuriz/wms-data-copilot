@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Falta el entorno virtual. Ejecuta primero: iniciar.cmd
  pause
  exit /b 1
)

if not "%~1"=="" (
  ".venv\Scripts\python.exe" -m wms_copilot.cli ask %*
  goto :end
)

set /p "Q=Pregunta: "
if "%Q%"=="" (
  echo No escribiste ninguna pregunta.
  goto :end
)
".venv\Scripts\python.exe" -m wms_copilot.cli ask "%Q%"

:end
echo.
pause
