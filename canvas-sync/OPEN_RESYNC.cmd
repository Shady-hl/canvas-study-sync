@echo off
setlocal
pushd "%~dp0"
call "%~dp0RUN_PYTHON.cmd" -B resync_app.py --launch %*
set "RESYNC_EXIT=%ERRORLEVEL%"
popd
if not "%RESYNC_EXIT%"=="0" pause
exit /b %RESYNC_EXIT%
