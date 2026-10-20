@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "CANVAS_PY=%~dp0.venv\Scripts\python.exe"
    goto run_python
  )
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 (
  goto run_launcher
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "CANVAS_PY=python"
  goto run_python
)
for %%V in (315 314 313 312 311 310 39) do (
  if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "CANVAS_PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
      goto run_python
    )
  )
)
set "CANVAS_RUNTIME=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CANVAS_RUNTIME%" (
  set "CANVAS_PY=%CANVAS_RUNTIME%"
  goto run_python
)
echo Python 3.11 or newer is required. Python 3.14 is supported.
echo Install Python from https://www.python.org/downloads/windows/ then reopen OPEN_RESYNC.cmd.
echo The app prepares PDF support automatically when you start your first build or sync.
exit /b 1

:run_launcher
py -3 -X utf8 %*
exit /b %ERRORLEVEL%

:run_python
"%CANVAS_PY%" -X utf8 %*
exit /b %ERRORLEVEL%
