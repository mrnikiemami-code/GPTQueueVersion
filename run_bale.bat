@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
if "%BALE_BOT_TOKEN%"=="" (
 echo ERROR: Set BALE_BOT_TOKEN before starting.
 exit /b 1
)
if "%BALE_ALLOWED_USER_IDS%"=="" (
 echo ERROR: Set BALE_ALLOWED_USER_IDS before starting.
 exit /b 1
)
set "GPTQUEUE_MESSENGER=bale"
echo Starting Bale C# adapter...
start "Bale Adapter" cmd /k "cd /d ""%ROOT%"" && dotnet run --project BaleAdapter/BaleAdapter.csproj --no-launch-profile"
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 30;$i++){try{$r=Invoke-RestMethod 'http://127.0.0.1:8768/health' -TimeoutSec 2;if($r.success){$ok=$true;break}}catch{};Start-Sleep -Seconds 1};if(-not $ok){exit 1}"
if errorlevel 1 (
 echo ERROR: Bale adapter is not ready.
 exit /b 1
)
echo Starting existing Vision queue in Bale mode...
"%PYTHON%" "%ROOT%\telegram_bot.py"
endlocal
