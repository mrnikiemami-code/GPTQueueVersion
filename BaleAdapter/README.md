# Bale transport: minimal C# replacement

This adds a .NET 8 compatibility transport for Bale while retaining the existing Python queue, Batch, SQLite behavior, Google Apps Script integration, Vision Bridge (port 8767), and Chrome extension.

The Python worker uses python-telegram-bot as a **compatible API client**; in Bale mode only its Bot API base URL and token are redirected to the local C# transport on 127.0.0.1:8768. The C# adapter forwards these calls to https://tapi.bale.ai. No job-processing business logic is duplicated.

## Setup on Windows

Install .NET 8 SDK and retain the existing Python environment. Create a Bale bot using Bale's BotFather.

Start the original Chrome extension and Bridge server on port 8767 as before. In a new PowerShell session:

```powershell
cd C:\ChatGPTQueueVersion
$env:BALE_BOT_TOKEN = "YOUR_BALE_TOKEN"
$env:BALE_ALLOWED_USER_IDS = "YOUR_BALE_USER_ID"
.\run_bale.bat
```

A Bale ID is not the same as a Telegram ID. First obtain it from the Bale app/bot setup; by design unknown users are denied until allowlisted.

- The Telegram launcher `run_bot.bat` remains unchanged.
- Bale mode saves jobs in `bale/jobs` and queue state in `bale/data/queue.db`. It does not alter Telegram jobs.
- Do **not** run two workers against the same ChatGPT browser and Bridge simultaneously.
- The token must stay outside Git. Do not put it in committed files.
- The C# transport binds to localhost only and checks the configured token on each API path.
- The proxy does not retry calls; existing Python behavior controls retries and job safety.

## Validation

```powershell
dotnet build .\BaleAdapter\BaleAdapter.csproj
Invoke-RestMethod http://127.0.0.1:8768/health
```

Verify with a real Bale bot before production: startup `getMe`, `/start`, `/myid`, photo upload, multi-photo batching, inline destination selection, progress message editing, `/retry`, delete group confirmation, Bridge JSON extraction and Google Sheet delivery. Bale API differences and service behavior need integration validation.

This environment did not have the .NET SDK or a Bale bot token; a successful real-world integration test has **not** yet been performed.
