@echo off
setlocal enabledelayedexpansion

rem -----------------------------------------------------------------------------
rem EasyTTS Model Converter - Run with MaiBotOneKey bundled Python (Windows)
rem
rem Behavior:
rem   - Uses MaiBot python by default (E:\bot\MaiBotOneKey\runtime\python31211\bin\python.exe)
rem   - If missing packages, ask to install; type y to auto pip install.
rem
rem You can override python path by setting:
rem   set MAIBOT_PYTHON=...\python.exe
rem -----------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
set "GUI_PY=%SCRIPT_DIR%easytts_model_converter_gui.py"

if defined MAIBOT_PYTHON (
  set "PY=%MAIBOT_PYTHON%"
) else (
  set "PY=E:\bot\MaiBotOneKey\runtime\python31211\bin\python.exe"
)

if not exist "%PY%" (
  echo [ERROR] Python not found: "%PY%"
  echo Please set MAIBOT_PYTHON to your MaiBot python.exe path.
  echo Example: set MAIBOT_PYTHON=E:\bot\MaiBotOneKey\runtime\python31211\bin\python.exe
  pause
  exit /b 1
)

if not exist "%GUI_PY%" (
  echo [ERROR] GUI script not found: "%GUI_PY%"
  pause
  exit /b 1
)

echo Using python: "%PY%"

rem Check required modules (tkinter is stdlib, but some embedded builds may miss it)
set "MISSING="
for /f "usebackq delims=" %%A in (`"%PY%" -c "import importlib.util; mods=['tkinter','genie_tts','torch']; miss=[m for m in mods if importlib.util.find_spec(m) is None]; print(' '.join(miss)); raise SystemExit(0 if not miss else 2)"`) do set "MISSING=%%A"

if not "%MISSING%"=="" (
  echo Missing packages: %MISSING%
  set /p ANSWER=Install missing packages now? (y/N):
  if /I "!ANSWER!"=="y" (
    echo Installing... (this may take a while)
    "%PY%" -m pip install -U pip
    "%PY%" -m pip install genie-tts torch
  ) else (
    echo Skipped install.
  )
)

echo Launching GUI...
"%PY%" "%GUI_PY%"

endlocal
