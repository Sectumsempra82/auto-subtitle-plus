@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\Build-Portable.ps1" -Installer %*
pause
