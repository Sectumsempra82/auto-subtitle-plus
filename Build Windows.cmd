@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\Build-Windows.ps1" %*
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (echo Windows build completed: a single EXE that asks Install or Portable when run.) else (echo Build failed. See the message and build log above.)
pause
exit /b %RESULT%
