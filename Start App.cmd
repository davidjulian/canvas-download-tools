@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run Install.cmd once before starting the app.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import playwright" >nul 2>&1
if errorlevel 1 (
  echo The app dependencies are missing. Run Install.cmd, then try again.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0local_app.py"
