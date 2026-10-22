@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -m venv .venv
  if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --log-level warning
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo ZeroTrace did not start. Check that Python 3.11 or later is installed and available as py.
pause
exit /b 1
