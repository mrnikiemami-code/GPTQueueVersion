$targets = @(
    'C:\ChatGPTQueueVersion\telegram_bot.py',
    'C:\ChatGPTQueueVersion\bridge_server.py'
)

foreach ($target in $targets) {

    $processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq 'python.exe' -and
            $_.CommandLine -and
            $_.CommandLine -like "*$target*"
        }

    foreach ($p in $processes) {

        Write-Host "Stopping PID $($p.ProcessId): $target"

        Stop-Process `
            -Id $p.ProcessId `
            -Force `
            -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1

Write-Host "Queue Bot/Bridge processes stopped."