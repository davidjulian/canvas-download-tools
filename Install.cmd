@echo off
setlocal
cd /d "%~dp0"
echo Installing the local Canvas Download Tools...
echo Python 3.10 or newer and Microsoft Edge or Google Chrome are required.
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>&1
if errorlevel 1 goto usepython
py -3 -c "import sys; sys.exit(sys.version_info < (3,10))"
if errorlevel 1 goto missingpython
py -3 -m venv .venv
if errorlevel 1 goto failed
goto dependencies
:usepython
python -c "import sys; sys.exit(sys.version_info < (3,10))"
if errorlevel 1 goto missingpython
python -m venv .venv
if errorlevel 1 goto failed
:dependencies
".venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
echo.
echo Setup complete. Double-click Start App.cmd to open the local interface.
pause
exit /b 0
:missingpython
echo Install Python 3.10 or newer with tkinter, then run Install.cmd again.
echo Use an institution-approved Python installation if required by your department.
pause
exit /b 1
:failed
echo Setup did not finish. Keep this window open to review the installation error above.
pause
exit /b 1
