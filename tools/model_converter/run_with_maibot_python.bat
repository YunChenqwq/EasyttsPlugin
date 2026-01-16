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
set "GENIETTS_REPO=%SCRIPT_DIR%..\genietts"

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

rem Check required modules.
rem NOTE: We DO NOT require installing the full `genie-tts` package on Windows (it may pull native deps like jieba_fast).
rem We use the locally cloned Genie-TTS repo in ..\genietts for conversion code.
set "MISSING="
set "TMP_MISS=%TEMP%\\easytts_missing_%RANDOM%.txt"
"%PY%" -c "import importlib.util; mods=['tkinter','torch','onnx']; miss=[m for m in mods if importlib.util.find_spec(m) is None]; print(' '.join(miss))" 1>"%TMP_MISS%" 2>nul
if exist "%TMP_MISS%" (
  for /f "usebackq delims=" %%A in ("%TMP_MISS%") do set "MISSING=%%A"
  del /q "%TMP_MISS%" >nul 2>&1
)

if not exist "%GENIETTS_REPO%\\src\\genie_tts" (
  echo [ERROR] Local Genie-TTS repo not found: "%GENIETTS_REPO%"
  echo Please clone it to: "%SCRIPT_DIR%..\\genietts"
  pause
  exit /b 1
)

if not "%MISSING%"=="" (
  echo Missing packages: %MISSING%
  rem NOTE: Don't use raw parentheses inside a (...) block in .bat (it breaks parsing).
  set /p ANSWER=Install missing packages now? y/N:
  if /I "!ANSWER!"=="y" (
    echo Installing... (this may take a while)
    "%PY%" -m pip install -U pip
    rem tkinter cannot be installed via pip for embedded Pythons; if missing, user needs a Python build with Tk.
    echo %MISSING% | findstr /i /c:"tkinter" >nul
    if not errorlevel 1 (
      echo [ERROR] tkinter is missing in this Python build. Please use a Python with Tk support.
      pause
      exit /b 2
    )

    rem Install torch only if missing (large download).
    echo %MISSING% | findstr /i /c:"torch" >nul
    if not errorlevel 1 (
      echo Installing torch...
      "%PY%" -m pip install torch
    )

    rem Install onnx (required by converter).
    echo %MISSING% | findstr /i /c:"onnx" >nul
    if not errorlevel 1 (
      echo Installing onnx...
      "%PY%" -m pip install onnx
    )
  ) else (
    echo Skipped install.
  )
)

echo Launching GUI...
set "GENIETTS_REPO=%GENIETTS_REPO%"
"%PY%" "%GUI_PY%"

endlocal
