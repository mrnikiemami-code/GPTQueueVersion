# GPTQueueVersion — MASTER RECOVERY / HANDOFF
**Updated:** 2026-10-13  
**Repository:** https://github.com/mrnikiemami-code/GPTQueueVersion  
**Branch:** `main`  
**User machine:** Windows; checkout `C:\ChatGPTQueueVersion`  
**Last verified code commit before this document:** `d13d5c71aee41db6564d9a7b8ea115361686bd88`  
**Protocol:** Read this file and inspect current GitHub HEAD/diff before any further implementation. Never assume this document proves runtime behavior.

## 0. Executive state — read first

User wants the ORIGINAL architecture and behavior preserved. The ONLY functional substitutions requested:
1. Switch the messenger from Telegram to **Bale** (بله) using an independent **C# / .NET 8 local compatibility transport** while retaining Python's existing queue/processing logic and the Chrome/Bridge/Google stack.
2. Optionally route Google Apps Script HTTP calls through **C#** in Bale mode instead of Python requests, with a simple one-line rollback.
3. Switching back to Telegram must remain easy and must not lose old Telegram jobs.

**User confirmed:** new Bale startup/Chrome/Bridge worked in their machine and the issue remaining was Google Sheet delivery. **NOT confirmed:** successful end-to-end Google delivery over the recently added C# relay; no independently verified production Bale callback/batch/retry matrix. The only post-relay evidence in this chat is configuration selection `GPTQUEUE_GOOGLE_TRANSPORT=dotnet`. Ask for actual logs before declaring Google fixed.

**Current behavior:**
- `run_bot.bat`: Telegram + direct Python Google HTTP (original route).
- `run_bale.bat`: Bale through local C# adapter; Google via C# by default, or direct Python by config.
- `telegram_bot.py` remains the *single source of truth* for Batch, Job persistence, serial queue, Vision JSON, Retry and Google delivery/outcome reconciliation. Despite its filename it runs both messenger modes.
- `BaleAdapter/Program.cs` is a minimal HTTP transport/proxy, NOT a rewritten C# bot business workflow.
- Need current `git status` and GitHub HEAD before edits; user's Windows worktree status has **not** been verified in this chat.

## 1. User's non-negotiable preferences

- Persian answers, precise and short, grounded in actual files/logs.
- Make changes yourself **in GitHub** using connected GitHub tools, provide exact commit ID and `git pull --ff-only origin main` command. Do not give large pasted fixes if repo access is working.
- Minimal surgical changes, preserve previously working code. Report exact files changed; inspect commit diffs after writes.
- Do not claim build/runtime tests succeeded when not run. Prefer focused verification and no redundant tests.
- Never put user credentials, API tokens, Google Sheet secret values or user identifiers into source-controlled recovery docs.
- User requires easy reversible Telegram/Bale and direct-Python/C#-relay selection.

## 2. Repository/runtime architecture

```
Messenger:
   Telegram Bot API (original), OR Bale Bot API
       Bale -> localhost:8768 .NET 8 BaleAdapter transport
       Telegram -> original PTB transport
                  |
           telegram_bot.py  [shared original logic]
             |         |
       persistent    delivery to Google Apps Script
       jobs/SQLite   through requests directly OR Bale-mode C# relay :8768
             |
     vision_bridge_client.py -> bridge_server.py 127.0.0.1:8767
                                -> ChromeBotProfile/Chrome Extension
                                -> chatgpt.com Vision
                                -> JSON 8 lead fields
```

Files:
- `telegram_bot.py`: main bot, allowlist, batch, durable SQLite queue, Retry/delete, Google Apps Script POST/GET, PTB startup.
- `vision_bridge_client.py`: Vision command/reply bridge HTTP client.
- `bridge_server.py`: local HTTP Bridge, port 8767.
- `ChatGPTBotExtension/content.js`: Chrome content script; DOM capture and Vision handling.
- `run_bot.bat`: ORIGINAL Telegram startup; runs `stop_all.ps1`, Chrome profile `ChromeBotProfile`, Bridge, then Python worker. Keep it working.
- `run_bale.bat`: Bale startup; loads `bale.local.cmd`, stops existing Python worker/Bridge, starts same Chrome profile, Bridge health check, then C# adapter, then same Python worker with Bale mode.
- `stop_all.ps1`: kills matching `telegram_bot.py` and `bridge_server.py` python.exe processes; **does not automatically stop C# BaleAdapter**.
- `BaleAdapter/BaleAdapter.csproj`: .NET 8 ASP.NET Core web project.
- `BaleAdapter/Program.cs`: HTTP proxy on loopback `127.0.0.1:8768`, routes `/bot<TOKEN>/<method>`, `/file/bot<TOKEN>/<path>` to `https://tapi.bale.ai`, and `/google/forward?target=...` to Google Apps Script.
- `BaleAdapter/README.md`: initial Bale setup/checklist (some text may predate changes to `run_bale.bat`).

Storage separation:
- Telegram: `jobs/` and `data/queue.db`.
- Bale mode: `bale/jobs/` and `bale/data/queue.db`.
- Bridge/Chrome profile are SHARED. Do NOT run two full workers against same chat/Bridge simultaneously. Bale old jobs NOT copied from Telegram.
- `config.json` contains current Telegram config and Google destinations; is gitignored. No need to rewrite destinations.

User's lead JSON fields MUST remain EXACT:
`instagramId`, `fullName`, `phoneNumber`, `job`, `employeeCount`, `city`, `challenge`, `description`.
The image-derived data is only trusted if captured from actual ChatGPT response; do not guess missing fields.

Two Google destinations:
- `negotiation_course` = فروش دوره مذاکره
- `azar_event` = همایش آذرماه
Apps Script URL ends `/exec`. Payload includes durable `requestId`, destination secret and optionally `imageBase64`, `imageFileName`, `imageMimeType`. POST once then status lookup/reconciliation.

## 3. Run/switch instructions (Windows PowerShell)

Fetch updates:
```powershell
cd C:\ChatGPTQueueVersion
git status --short
git pull --ff-only origin main
```
If dirty worktree, inspect BEFORE pull; never force-reset or discard runtime state.

Telegram rollback:
1. Stop Bale Python window with Ctrl+C, and **close Bale Adapter .NET window**.
2. Run `C:\ChatGPTQueueVersion\run_bot.bat`.
3. No edits to Bale secrets are needed.
4. Original Telegram uses Python Google requests even if Bale config says `dotnet`.

Bale:
1. Stop Telegram Python worker; close any stale BaleAdapter C# process to avoid port 8768 conflict.
2. Ensure `.NET 8 SDK` installed; one-time `dotnet build .\BaleAdapter\BaleAdapter.csproj`.
3. The machine-local `bale.local.cmd` MUST exist at repo root; `.gitignore` excludes it. NEVER print its contents or commit it.
4. `run_bale.bat` initializes Chrome and Bridge, starts C# adapter and Python worker. To run ` .\run_bale.bat `.

**Credential template ONLY (no real token/ID):**
```bat
@echo off
set "BALE_BOT_TOKEN=<NEW_PRIVATE_TOKEN>"
set "BALE_ALLOWED_USER_IDS=<BALE_USER_IDS_COMMA_SEPARATED>"
rem Google transport selection: only ONE active line
set "GPTQUEUE_GOOGLE_TRANSPORT=dotnet"
rem set "GPTQUEUE_GOOGLE_TRANSPORT=python"
```
Bale user IDs are different from Telegram IDs. Token was disclosed in this conversation/screenshots; advise user to rotate via Bale bot management and store new token only locally. Do NOT copy disclosed secret into any artifact.

`bale.local.secret` caused Windows "choose an application" because `cmd call` cannot execute a `.secret` extension. Fixed by switching to **`bale.local.cmd`**, which is explicitly gitignored. Do not regress to `call bale.local.secret`.

Google transport:
- Bale default `GPTQUEUE_GOOGLE_TRANSPORT=dotnet`.
- For original direct Python route while staying in Bale: uncomment `set "GPTQUEUE_GOOGLE_TRANSPORT=python"`, comment out `dotnet` line, restart.
- Telegram mode ignores this switch for Google, regardless of residual OS env setting.
- No C# automatic POST retry. Python continues to use its original `requestId` and `delivery_unknown` safety semantics.
- Original GOOGLE AUTO POST RETRY = 0. Manual /retry first checks stored status; avoid repeated POST on unknown delivery.
- Important: system-wide existing variables may affect mode if not explicitly set in `bale.local.cmd`; inspect emitted `Google transport: ...` log.

## 4. Network / Google troubleshooting evidence

Original problem while Telegram+Python:
- Vision succeeded: image -> correct JSON once, then Google POST had `Read timed out. (read timeout=5)` although configured connect=5/read=30.
- Status GET checks mixed 200 `not_found` with errors read timeout=4 (configured connect=4/read=8).
- Code intentionally returned `delivery_unknown` and did not blindly resubmit.
- Correct interpretation: `read timeout=5/4` **may** arise during HTTPS proxy/tunnel establishment despite higher read timeout; evidence did NOT conclusively prove cause.
- No environment HTTP_PROXY/HTTPS_PROXY was set and WinHTTP said Direct. These do NOT rule out Proxifier intercepting traffic.
- Proxifier screenshot showed `python.exe` -> SOCKS5 `127.0.0.1` for all except localhost destinations, localhost Direct. That suggests route, but logs for actual Google failure were not supplied.
- Python GET test: `www.google.com` timed out at ~5.25 sec, `script.google.com` 302 in .82 sec, `api.telegram.org` 302 in .61 sec.
- Five `script.google.com` status GETs without auth returned HTTP 200 JSON `UNAUTHORIZED` in ~2.24–5.67 sec. Means network reachable, NOT authorized and NOT proof POST works.
- Chrome may use different network path than Python.
- Crucial new concern: Proxifier python.exe rule does **NOT automatically guarantee dotnet.exe** or running C# app's process uses SOCKS5; must inspect Proxifier log/rules while C# Google relay sends to `script.google.com:443`. Dotnet may use different network path. No confirmation yet.
- Need check Google Apps Script POST semantics: normal ContentService POST returns 302 redirect; no auto-follow on POST; GET status can follow redirects.
- C# relay `google-post`: 85-second HttpClient timeout, redirects disabled.
- C# relay `google-get`: 85-second HttpClient timeout, redirects enabled.
- Python relay-mode localhost `requests.get/post` read timeout 75 sec, connect original 4/5. Python status has six attempts, initial delay 1.5s and 2s between attempts; semantics preserved.
- If C# responds 502 after upstream failure, Python's existing `requests.RequestException` / status handling should continue to classify unknown delivery, not invent a successful sheet write.

## 5. Commit trail (all in main; verify actual current HEAD)

- `8ea6241a34ebb4a5fc7a1b37ba523cf67c36a9ce`: Chrome extension fix: capture newer ChatGPT document-preview `MarkdownRoot` JSON overlay, avoid stale result.
- `bb2178391fced7a524a5ee16e64572df0a1e4526`: eliminate double JSON stringification (`text: visionJson`).
- `42f9f1b613b4e774cb4af0caa8d763f152f1afd9`: remove premature grouped-duplicate delete guard in /retry; existing group-confirm mechanism retained.
- `89a1d9f506fd437b6bc86d3a5cb4dd900e54d5f1`: Bale .NET project.
- `31b39ce29889dab0325b51ae2045c5d5a6619870`: Bale C# transport.
- `5e09b77050ba75305aaa91c3d7972c1915669e34`: Bale launcher skeleton.
- `3010fbbd57f3d5e13617d21a3f66e91431891b9e`: Python switch `GPTQUEUE_MESSENGER=bale`, separate Bale jobs/DB, allowlist, base URLs.
- `1865098d213a78b13df26263a821e16f3cc488a4`: Bale setup doc.
- `a87c564db5c4b7522336846a9ac9fbc31a4a83a5`: fix Windows adapter launch.
- `f6f9ec9febab5173dac9a859c3d63defcc281519`: initial `.secret` loader (superseded).
- `cce61d419be8d345ef62f98f7f1338fded7c09f5`, `768670f44e1a13b5e7b5377aa7e065e9d0c5519f`: gitignore `bale.local.cmd`, switch loader to CMD.
- `40aa7430ce58703ddea4d1d592189741500c0f86`: fix Bale startup: Chrome/Bridge same as Telegram.
- `3fc20c10964181a0bdba72de63e2bf4384abe74a`: .NET Google forward endpoint.
- `4ef681fb94d2348270f0ac95401c480beda10956`: Python optional C# Google relay route.
- `d13d5c71aee41db6564d9a7b8ea115361686bd88`: Bale default C# Google relay; one-setting rollback.

## 6. Open issues / risks — prioritized

**P0 Verify end-to-end Google relay:** Need one actual Bale image, C# `GOOGLE_RELAY method=POST status=...` and `GET` logs, Python `GOOGLE SHEET RESPONSE` and actual Sheet row or unequivocal status confirmation. If `delivery_unknown`, never infer success or safe retry without checking requestId in Sheet first.

**P0 Verify C# process network route:** Inspect Proxifier `dotnet.exe`/app process -> `script.google.com` and `tapi.bale.ai`. Establish whether SOCKS5 is applied to .NET. Try one small GET/status before image upload, do not test with real lead multiple times.

**P1 Runtime build/integration:** GitHub files were committed and diffs checked, but .NET build and a full Bale integration suite were NOT independently run in assistant environment. User said 'everything okay' before Google-path change; this is informal user-reported confirmation. In new chat first request/log `dotnet build` if needed, not unbounded tests.

**P1 Bale/API compatibility:** Although Bale is Telegram-style, details of file path, edited messages, CallbackQuery, media groups, long polling, PTB validators may differ. Explicitly test actual /start/photo/inline choice/Retry/delete group when relevant. Do not claim exact compatibility without evidence.

**P1 Google relay security:** The localhost `/google/forward` currently only checks Google host/path, has no separate auth header; localhost only mitigates external exposure. Avoid widening listener to 0.0.0.0. GET redirects are followed by HttpClient; inspect real redirect behavior. No retries at relay; preserve POST-at-most-once semantics.

**P1 Service lifecycle:** `stop_all.ps1` kills Python workers and Bridge but not `dotnet` adapter. Closing Bale main Python console may leave adapter up. On mode switching, explicitly stop/close adapter window; avoid starting two adapters on 8768.

**P2 Startup warning:** `PTBUserWarning: Application.create_task while application is not running` at startup; Vision worker did execute despite warning. Don't conflate warning with Google timeout.

**P2 Proxifier/slow network:** network instability appears across Google/Telegram; underlying network failure may persist despite changing HTTP transport to C#.

**P2 Docs drift:** `BaleAdapter/README.md` was created before local `bale.local.cmd` and Chrome-startup fixes; consider updating it to match this recovery document, but only as a separate scoped improvement if needed.

**Data/privacy:** User-supplied bot token has been shown in chat and a screenshot. Never restate it; user should rotate; do not log full target query, headers or tokens; `config.json`, `bale.local.cmd`, Chrome user profile and SQLite runtime data are not to be committed.

## 7. What to do on a new chat

1. User can say: **«ریکاوری پروژه GPTQueueVersion را از فایل `GPTQUEUE-MASTER-RECOVERY.md` در GitHub بخوان، HEAD و diff را بررسی کن، بعد ادامه بده.»**
2. Connect to GitHub repo; open this file; compare `main` HEAD and relevant source files. If the user refers to runtime logs, ask only for needed concrete log lines.
3. Establish active mode: Telegram or Bale; Google route python or dotnet; current Win process/Proxifier routing.
4. Check status of previous task before touching code; maintain rollback.
5. Restrict scope to current defect, commit, verify changed-file list/diff, provide exact `git pull --ff-only origin main` command.
6. Never autonomously migrate whole architecture to C#; the user's stated intent is **C# transport only**, original Python queue retained.
7. After an ambiguous Google POST, do NOT blindly retry or delete unconfirmed jobs.

## 8. Current exact next action

If asked to continue troubleshooting: capture one **single** Bale test job, matching:
- C# console: `GOOGLE_RELAY method=POST status=...`, `GOOGLE_RELAY method=GET status=...` or `GOOGLE_RELAY_ERROR`.
- Python: `GOOGLE POST`, `GOOGLE STATUS`, `GOOGLE SHEET RESPONSE`, specific `requestId`.
- Proxifier: executable and route used for `script.google.com:443`.
- Actual Google Sheet row or status response confirming saved/not found.

Handle secrets by redacting them, and do not change transport/timeout or Retry semantics until evidence identifies cause.
