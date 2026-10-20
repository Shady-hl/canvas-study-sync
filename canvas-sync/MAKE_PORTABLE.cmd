@echo off
setlocal
pushd "%~dp0"
call "%~dp0RUN_PYTHON.cmd" ai_export.py --portable %*
set "STUDY_EXIT=%ERRORLEVEL%"
popd
pause
exit /b %STUDY_EXIT%
