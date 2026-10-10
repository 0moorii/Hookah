@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
if defined HOOKAH_PYTHON goto custom
if exist "%~dp0.venv\Scripts\python.exe" goto venv
where py >nul 2>&1
if not errorlevel 1 goto launcher
where python >nul 2>&1
if not errorlevel 1 goto python
>&2 echo Python 3.10+ is required. Install Python, then run Setup-Manager.cmd.
exit /b 1
:custom
"%HOOKAH_PYTHON%" -B "%~dp0snmp_data_collector_v12.py" %*
exit /b %errorlevel%
:venv
"%~dp0.venv\Scripts\python.exe" -B "%~dp0snmp_data_collector_v12.py" %*
exit /b %errorlevel%
:launcher
py -3 -B "%~dp0snmp_data_collector_v12.py" %*
exit /b %errorlevel%
:python
python -B "%~dp0snmp_data_collector_v12.py" %*
exit /b %errorlevel%
