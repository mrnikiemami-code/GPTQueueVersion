@echo off
setlocal

title ChatGPT Queue Telegram Bot

cd /d C:\ChatGPTQueueVersion

set "ROOT=C:\ChatGPTQueueVersion"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "PROFILE=%ROOT%\ChromeBotProfile"

if not exist "%CHROME%" (
    set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)

echo.
echo ========================================
echo       ChatGPT Queue Telegram Bot
echo ========================================
echo.

echo [1/5] Stopping old Queue Bot / Queue Bridge...
echo.

powershell.exe ^
  -NoProfile ^
  -ExecutionPolicy Bypass ^
  -File "%ROOT%\stop_all.ps1"

echo.
echo [2/5] Starting Queue Chrome profile...
echo.

start "" "%CHROME%" ^
  --user-data-dir="%PROFILE%" ^
  --disable-backgrounding-occluded-windows ^
  --disable-renderer-backgrounding ^
  "https://chatgpt.com/"

echo.
echo [3/5] Starting Queue Local Bridge...
echo.

start "ChatGPT Queue Bridge" cmd /k ^
  ""%PYTHON%" "%ROOT%\bridge_server.py""

echo.
echo [4/5] Waiting for Queue Bridge...
echo.

powershell.exe -NoProfile -Command ^
  "$ok=$false; for($i=0;$i -lt 20;$i++){ try { $r=Invoke-RestMethod 'http://127.0.0.1:8767/health' -TimeoutSec 2; if($r.success){$ok=$true;break} } catch {}; Start-Sleep -Seconds 1 }; if(-not $ok){exit 1}"

if errorlevel 1 (
    echo.
    echo ========================================
    echo ERROR: Queue Bridge did not start.
    echo ========================================
    echo.
    pause
    exit /b 1
)

echo.
echo Queue Bridge is READY.

echo.
echo [5/5] Starting Queue Telegram Bot...
echo.

"%PYTHON%" "%ROOT%\telegram_bot.py"

echo.
echo ========================================
echo          QUEUE BOT STOPPED
echo ========================================
echo.

pause

endlocal