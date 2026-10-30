@echo off
rem ===========================================================================
rem  HC Agent - Native Desktop Launcher
rem
rem  Launches the NATIVE desktop client (Tk), NOT a web page.
rem  There is no HTTP server, no port, and no browser engine involved.
rem
rem  Uses pythonw.exe so no console window appears behind the UI. A console
rem  window is the fastest way to make a desktop app look unfinished.
rem
rem  ASCII-only on purpose: cmd.exe parses .bat in the ANSI code page, so
rem  UTF-8 Chinese in comments gets executed as commands.
rem ===========================================================================
setlocal
title HC Agent

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "PROJECT=%APP_DIR%\.."

set "PYW="
if exist "%PROJECT%\.venv\Scripts\pythonw.exe" set "PYW=%PROJECT%\.venv\Scripts\pythonw.exe"
if not defined PYW if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" set "PYW=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
if not defined PYW (
  for %%P in (pythonw.exe) do if not defined PYW set "PYW=%%~$PATH:P"
)

if not defined PYW (
  echo.
  echo   [ERROR] pythonw.exe not found.
  echo   The desktop client is written in Python + Tk and needs a Python runtime.
  echo.
  pause
  exit /b 1
)

rem  Tell the backend watchdog a supervisor exists; without this it refuses to
rem  enable its self-healing exit (exit code 70), by design.
set "HC_WATCHDOG_SUPERVISOR=1"
rem  Never inherit a Node/Electron mode flag into the Python child.
set "ELECTRON_RUN_AS_NODE="
set "NODE_OPTIONS="
set "PYTHONIOENCODING=utf-8"

cd /d "%PROJECT%"
rem  Deliberately NOT `start "" /b`. The /b flag attaches the child to THIS
rem  console, so pythonw dies with the launching shell -- double-click appeared
rem  to work, then the app vanished when the console closed. A plain `start ""`
rem  gives the GUI its own process, independent of this console. pythonw.exe is
rem  a GUI-subsystem binary, so no console window flashes either.
start "" /d "%PROJECT%" "%PYW%" -m hc_agent.desktop
exit /b 0
