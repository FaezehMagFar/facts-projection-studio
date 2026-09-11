@echo off
setlocal
set "FACTS_UI_DIR=%~dp0"

if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
  start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
)

wsl.exe -d Ubuntu --cd "%FACTS_UI_DIR%" -- bash ./facts-dashboard
if errorlevel 1 (
  echo.
  echo FACTS Dashboard could not start. Review the message above.
  pause
)
