@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
rem Optional machine-local settings (ignored by Git via *.secret).
rem Never commit credentials to this public repository.
if exist "%ROOT%\bale.local.cmd" call "%ROOT%\bale.local.cmd"
if "%BALE_BOT_TOKEN%"=="" (
 echo ERROR: Set BALE_BOT_TOKEN before starting.
 exit /b 1
)
if "%BALE_ALLOWED_USER_IDS%"=="" (
 echo ERROR: Set BALE_ALLOWED_USER_IDS before starting.
 exit /b 1
)
set "GPTQUEUE_MESSENGER=bale"
rem Set GPTQUEUE_GOOGLE_TRANSPORT=python in bale.local.cmd for the original direct route.
if "%GPTQUEUE_GOOGLE_TRANSPORT%"=="" set "GPTQUEUE_GOOGLE_TRANSPORT=dotnet"
echo Google transport: %GPTQUEUE_GOOGLE_TRANSPORT%
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
set "PROFILE=%ROOT%\ChromeBotProfile"
if not exist "%CHROME%" (
 echo ERROR: Chrome executable not found.
 exit /b 1
)
echo [1/4] Stopping old Python queue and Bridge...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\stop_all.ps1"
echo [2/4] Starting Chrome using existing Queue profile...
start "" "%CHROME%" --user-data-dir="%PROFILE%" --disable-backgrounding-occluded-windows --disable-renderer-backgrounding "https://chatgpt.com/"
echo [3/4] Starting existing Bridge...
start "ChatGPT Queue Bridge" cmd /k ""%PYTHON%" "%ROOT%\bridge_server.py""
powershell.exe -NoProfile -Command "$ok=$false; for($i=0;$i -lt 20;$i++){try{$r=Invoke-RestMethod 'http://127.0.0.1:8767/health' -TimeoutSec 2;if($r.success){$ok=$true;break}}catch{};Start-Sleep -Seconds 1};if(-not $ok){exit 1}"
if errorlevel 1 (
 echo ERROR: Queue Bridge did not start.
 exit /b 1
)
echo [4/4] Starting Bale adapter and Python queue...
echo Starting Bale C# adapter...
start "Bale Adapter" /D "%ROOT%" cmd /k dotnet run --project BaleAdapter\BaleAdapter.csproj --no-launch-profile
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 30;$i++){try{$r=Invoke-RestMethod 'http://127.0.0.1:8768/health' -TimeoutSec 2;if($r.success){$ok=$true;break}}catch{};Start-Sleep -Seconds 1};if(-not $ok){exit 1}"
if errorlevel 1 (
 echo ERROR: Bale adapter is not ready.
 exit /b 1
)
echo Starting existing Vision queue in Bale mode...
"%PYTHON%" "%ROOT%\telegram_bot.py"
endlocal
