@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\smoke.ps1" %*
exit /b %ERRORLEVEL%
