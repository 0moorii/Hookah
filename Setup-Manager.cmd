@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\setup-manager.ps1" %*
exit /b %errorlevel%
