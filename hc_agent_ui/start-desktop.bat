@echo off
rem ===========================================================================
rem  HC Agent - Desktop Launcher
rem
rem  Why this .bat exists instead of pointing the shortcut straight at
rem  electron.exe:
rem    1) Electron needs the app directory as an argument. Pointing at
rem       electron.exe alone opens Electron's default screen, not this app.
rem    2) Hosts (IDEs, terminals, CLI tools) often inject NODE_OPTIONS and
rem       ELECTRON_RUN_AS_NODE. The latter makes Electron degrade into plain
rem       Node, so no window ever appears. Both must be cleared first.
rem    3) The backend watchdog only enables its self-healing exit when it
rem       knows a supervisor exists, hence HC_WATCHDOG_SUPERVISOR=1.
rem
rem  This file is intentionally ASCII-only. cmd.exe parses .bat in the ANSI
rem  code page, so UTF-8 Chinese in comments gets executed as commands.
rem ===========================================================================
rem  THE TWO FLAGS THAT MAKE ELECTRON ACTUALLY START (measured, not guessed):
rem    --no-sandbox            Without it Electron dies with 0x80000003
rem                            (STATUS_BREAKPOINT) BEFORE any JS runs -- no
rem                            window, no log, nothing to debug with.
rem    --user-data-dir=<path>  Without it Electron dies with 0xC0000005
rem                            (access violation) inside app.getPath("userData"),
rem                            which is the first thing main.js touches.
rem  Confirmed by A/B test on one machine: same app, only these flags differ.
rem  Without them the process is dead; with them it stays alive and main.js runs.
rem ===========================================================================
setlocal
title HC Agent - Distribution Hosting Capacity Agent

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

set "NODE_OPTIONS="
set "ELECTRON_RUN_AS_NODE="
set "ELECTRON_NO_ATTACH_CONSOLE="
set "HC_WATCHDOG_SUPERVISOR=1"

rem Profile must sit on a writable path; a sibling of the app dir is easy to
rem find and easy to delete when it goes bad.
set "PROFILE=%APP_DIR%\profile"
if not exist "%PROFILE%" mkdir "%PROFILE%" 2>nul

set "ELECTRON=%APP_DIR%\node_modules\electron\dist\electron.exe"
if not exist "%ELECTRON%" (
  echo.
  echo   [ERROR] Electron not found:
  echo   %ELECTRON%
  echo.
  echo   Install it with:
  echo     cd /d "%APP_DIR%"
  echo     npm install
  echo.
  pause
  exit /b 1
)

set "PY="
if exist "%APP_DIR%\..\.venv\Scripts\python.exe" set "PY=%APP_DIR%\..\.venv\Scripts\python.exe"
if not defined PY if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY (
  for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
)

echo.
echo   ============================================================
echo     Distribution Hosting Capacity Agent  -  Desktop
echo   ============================================================
echo.
echo     App dir : %APP_DIR%
if defined PY (echo     Python  : %PY%) else (echo     Python  : NOT FOUND - backend cannot start)
echo     Profile : %PROFILE%
echo.
echo     A window will open shortly.
echo     First launch may take 20-40s to load OpenDSS.
echo     Closing the window stops the backend too.
echo.
echo   ============================================================
echo.

cd /d "%APP_DIR%"
"%ELECTRON%" --no-sandbox --disable-gpu --disable-gpu-compositing --disable-gpu-sandbox --user-data-dir="%PROFILE%" "%APP_DIR%"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo   [ERROR] Exit code: %RC%
  if "%RC%"=="70" echo   ^(watchdog requested a restart - the shell handles this^)
  if "%RC%"=="-1073741819" echo   ^(access violation - delete the Profile dir above and retry^)
  if "%RC%"=="-2147483645" echo   ^(sandbox init failed - check antivirus / EDR policy^)
  echo.
  echo   Logs: %APPDATA%\hc-agent-ui\logs\
  echo.
  pause
)
