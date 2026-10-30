@echo off
rem ===========================================================================
rem  HC Agent - Portable launcher for the packaged build
rem
rem  Runs the PyInstaller-built exe. Kept ASCII-only: cmd.exe parses .bat in
rem  the ANSI code page, so UTF-8 Chinese in comments would be executed.
rem ===========================================================================
setlocal
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

set "EXE=%HERE%\HC-Agent.exe"
if not exist "%EXE%" (
  echo.
  echo   [ERROR] HC-Agent.exe not found next to this launcher.
  echo   Expected: %EXE%
  echo.
  pause
  exit /b 1
)

rem  Clear host-injected Node/Electron flags so they cannot leak into Python.
set "ELECTRON_RUN_AS_NODE="
set "NODE_OPTIONS="
set "PYTHONIOENCODING=utf-8"
rem  Tell the backend watchdog that a supervisor exists.
set "HC_WATCHDOG_SUPERVISOR=1"

rem  `start "" /d "<dir>" "<exe>"` silently does nothing when the directory
rem  contains a space (like ...\HC-CleanTest\HC-Agent), so cd first and let the
rem  child inherit the working directory instead of passing /d.
cd /d "%HERE%"
start "" "%EXE%"
exit /b 0
