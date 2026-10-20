@echo off
setlocal
pushd "%~dp0"
echo Updating the archive containing this tool folder. Use its 00_START_HERE.md guide.
call "%~dp0RUN_PYTHON.cmd" check_dependencies.py
if errorlevel 1 (
  popd
  pause
  exit /b 1
)
call "%~dp0RUN_PYTHON.cmd" -B study_update.py --sync %*
set "STUDY_EXIT=%ERRORLEVEL%"
if "%STUDY_EXIT%"=="2" echo Output prepared with notices. Check the coverage reports.
if "%STUDY_EXIT%"=="1" echo An error occurred. Read the message above.
popd
pause
exit /b %STUDY_EXIT%
