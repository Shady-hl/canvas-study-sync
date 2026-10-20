@echo off
setlocal
pushd "%~dp0"
echo Installing the PDF reader and timezone data for the Python used by this setup.
call "%~dp0RUN_PYTHON.cmd" -m pip install -r "%~dp0requirements-ai.txt"
set "STUDY_EXIT=%ERRORLEVEL%"
if not "%STUDY_EXIT%"=="0" goto finished
call "%~dp0RUN_PYTHON.cmd" check_dependencies.py
set "STUDY_EXIT=%ERRORLEVEL%"
if "%STUDY_EXIT%"=="0" echo Setup complete. You can now run UPDATE_MATERIALS.cmd.
:finished
popd
pause
exit /b %STUDY_EXIT%
