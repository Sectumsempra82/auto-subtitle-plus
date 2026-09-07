@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\Build-Portable.ps1" %*
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (echo Portable build completed.) else (echo Build failed. See the message and build log above.)
pause
exit /b %RESULT%
