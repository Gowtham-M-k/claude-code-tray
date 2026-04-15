@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM Claude Code Tray — Windows setup
REM Installs deps + adds a startup shortcut to the current user's
REM Startup folder so the tray icon launches at logon.
REM ─────────────────────────────────────────────────────────────────────────
setlocal ENABLEDELAYEDEXPANSION

set "SCRIPT_DIR=%~dp0"
set "PY=python"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP%\ClaudeCodeTray.lnk"
set "LOG=%USERPROFILE%\.claude-tray.log"

echo [1/3] Installing Python dependencies...
%PY% -m pip install -q -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo ERROR: pip install failed. Make sure Python is on PATH.
    pause & exit /b 1
)

echo [2/3] Creating startup shortcut in:
echo        %STARTUP%

REM Use PowerShell to create a proper .lnk shortcut
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$sc = $ws.CreateShortcut('%SHORTCUT%');" ^
  "$sc.TargetPath = '%PY%';" ^
  "$sc.Arguments = '\"%SCRIPT_DIR%claude_tray.py\"';" ^
  "$sc.WorkingDirectory = '%SCRIPT_DIR%';" ^
  "$sc.WindowStyle = 7;" ^
  "$sc.Description = 'Claude Code Status Tray';" ^
  "$sc.Save()"

if errorlevel 1 (
    echo WARNING: Could not create shortcut — add it manually.
)

echo [3/3] Launching tray in background...
start "" /B %PY% "%SCRIPT_DIR%claude_tray.py" >> "%LOG%" 2>&1

echo.
echo  Done!  The tray icon should appear in your system tray area.
echo  If you don't see it, click the ^ arrow in the taskbar.
echo  Log file: %LOG%
echo.
pause
