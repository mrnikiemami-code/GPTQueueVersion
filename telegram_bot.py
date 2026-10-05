import os
import sys
import json
import asyncio
import tempfile
import traceback
import ctypes
import requests
import base64
import mimetypes
import shutil
import uuid
import time
import re
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from vision_bridge_client import analyze_image_via_bridge


# =========================================================
# SINGLE INSTANCE
# =========================================================

BOT_MUTEX_NAME = "Local\\ChatGPTQueueVersion_SingleInstance"

_bot_mutex = ctypes.windll.kernel32.CreateMutexW(
    None,
    False,
    BOT_MUTEX_NAME
)

if ctypes.windll.kernel32.GetLastError() == 183:
    print("")
    print("ERROR: Another Telegram Bot instance is already running.")
    print("")
    input("Press Enter to exit...")
    sys.exit(1)


# =========================================================
# PATHS + CONFIG
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
DATA_DIR = os.path.join(BASE_DIR, "data")
QUEUE_DB_PATH = os.path.join(DATA_DIR, "queue.db")
os.makedirs(JOBS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Queue-version batching behavior. Photos received from the same user within
# this quiet window are grouped into one batch and get ONE destination prompt.
BATCH_COLLECT_SECONDS = 3.0

DESTINATION_COURSE = "negotiation_course"
DESTINATION_AZAR_EVENT = "azar_event"

DESTINATION_LABELS = {
    DESTINATION_AZAR_EVENT: "همایش آذرماه",
    DESTINATION_COURSE: "فروش دوره مذاکره",
}



def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config():
    with open(
        CONFIG_PATH,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            config,
            f,
            ensure_ascii=False,
            indent=2
        )


config = load_config()

BOT_TOKEN = config.get("telegramBotTokenQueue") or config["telegramBotToken"]


# =========================================================
# NETWORK / RETRY SETTINGS
#
# همه زمان‌ها از اینجا قابل تغییر هستند.
# برای تغییر رفتار Google Sheet فقط این مقادیر را عوض کن.
# =========================================================

# حداکثر زمان انتظار برای برقراری اتصال POST
GOOGLE_POST_CONNECT_TIMEOUT_SECONDS = 5

# حداکثر زمان انتظار برای پاسخ POST بعد از اتصال
# طبق تصمیم فعلی: 15 ثانیه
GOOGLE_POST_READ_TIMEOUT_SECONDS = 15

# timeout بررسی وضعیت requestId
GOOGLE_STATUS_CONNECT_TIMEOUT_SECONDS = 4
GOOGLE_STATUS_READ_TIMEOUT_SECONDS = 8

# چند بار وضعیت requestId بررسی شود
GOOGLE_STATUS_POLL_ATTEMPTS = 3

# کمی صبر قبل از اولین status check
GOOGLE_STATUS_INITIAL_DELAY_SECONDS = 0.75

# فاصله بین بررسی‌های وضعیت
GOOGLE_STATUS_POLL_DELAY_SECONDS = 1.0

# Retry خودکار POST غیرفعال است.
# اگر ثبت تأیید نشد، Job محفوظ می‌ماند و کاربر با /retry دوباره تلاش می‌کند.
GOOGLE_AUTO_POST_RETRY_COUNT = 0

# timeout پیام‌های وضعیت Telegram
TELEGRAM_STATUS_MESSAGE_TIMEOUT_SECONDS = 8

# Hard upper bound for one Vision job as observed by the Telegram worker.
# The underlying Bridge call runs in a worker thread, so the outer asyncio
# timeout is authoritative and prevents the queue from hanging indefinitely.
VISION_JOB_TIMEOUT_SECONDS = 60

# =========================================================
# TELEGRAM NETWORK SETTINGS
#
# اگر مسیر Telegram/VPN ناپایدار بود، فقط این مقادیر را تغییر بده.
# این تنظیمات به Vision و Google Sheet دست نمی‌زنند.
# =========================================================

# درخواست‌های معمول Bot API مثل sendMessage/getFile/download
TELEGRAM_CONNECT_TIMEOUT_SECONDS = 10
TELEGRAM_READ_TIMEOUT_SECONDS = 20
TELEGRAM_WRITE_TIMEOUT_SECONDS = 20
TELEGRAM_POOL_TIMEOUT_SECONDS = 10
TELEGRAM_CONNECTION_POOL_SIZE = 16

# Long polling برای getUpdates
TELEGRAM_POLL_CONNECT_TIMEOUT_SECONDS = 10
TELEGRAM_POLL_READ_TIMEOUT_SECONDS = 45
TELEGRAM_POLL_WRITE_TIMEOUT_SECONDS = 10
TELEGRAM_POLL_POOL_TIMEOUT_SECONDS = 10
TELEGRAM_POLL_CONNECTION_POOL_SIZE = 2

# Telegram long-poll timeout sent to Telegram servers
TELEGRAM_LONG_POLL_TIMEOUT_SECONDS = 30

# فاصله کوتاه بین polling cycleها؛ صفر یعنی بدون تأخیر اضافه
TELEGRAM_POLL_INTERVAL_SECONDS = 0.0

# در Restart پیام‌های pending را دور نریز.
TELEGRAM_DROP_PENDING_UPDATES = False


# =========================================================
# TELEGRAM ACCESS CONTROL
# =========================================================
#
# فقط User ID افراد مجاز را اینجا قرار بده.
# برای دیدن User ID هر شخص، در تلگرام دستور /myid را بزند.
#

ALLOWED_USER_IDS = {
     833081449,   
     154892792
}


def get_telegram_user_id(update: Update):

    user = update.effective_user

    if user is None:
        return None

    return user.id


def is_allowed_user(update: Update):

    user_id = get_telegram_user_id(update)

    if user_id is None:
        return False

    return user_id in ALLOWED_USER_IDS


async def deny_access(update: Update):

    user = update.effective_user

    if user is None:
        return

    username = (
        f"@{user.username}"
        if user.username
        else "-"
    )

    print("")
    print("UNAUTHORIZED TELEGRAM USER")
    print("User ID:", user.id)
    print("Username:", username)
    print("")

    if update.message:
        await update.message.reply_text(
            "⛔ شما اجازه استفاده از این ربات را ندارید.\n\n"
            f"Telegram User ID: {user.id}\n\n"
            "    "
        )




def get_google_sheet_config(destination=DESTINATION_COURSE):
    """
    Destination-aware Google Sheet config with backward compatibility.

    Current production config continues to work unchanged:
        config["googleSheet"]

    Unified-bot config can additionally use:
        config["googleSheets"]["negotiation_course"]
        config["googleSheets"]["azar_event"]
    """
    destination = destination or DESTINATION_COURSE

    google_sheets = config.get("googleSheets")
    if isinstance(google_sheets, dict):
        destination_config = google_sheets.get(destination)
        if isinstance(destination_config, dict):
            return destination_config

    if destination == DESTINATION_COURSE:
        legacy_config = config.get("googleSheet")
        if isinstance(legacy_config, dict):
            return legacy_config

    return {}


def get_google_sheet_url(destination=DESTINATION_COURSE):
    return str(
        get_google_sheet_config(destination).get("url") or ""
    ).strip()


def get_google_sheet_secret(destination=DESTINATION_COURSE):
    return str(
        get_google_sheet_config(destination).get("secret") or ""
    ).strip()


def is_destination_configured(destination):
    return bool(
        get_google_sheet_url(destination)
        and get_google_sheet_secret(destination)
    )


# =========================================================
# PROCESSING LOCK
# =========================================================

processing_lock = asyncio.Lock()


# =========================================================
# PERSISTENT BATCH + VISION QUEUE (SQLite)
# =========================================================

batch_state_lock = asyncio.Lock()
batch_finalize_tasks = {}
queue_worker_task = None


def _db_connect():
    connection = sqlite3.connect(
        QUEUE_DB_PATH,
        timeout=30,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def init_queue_db():
    with _db_connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                telegram_user_id INTEGER NOT NULL,
                telegram_chat_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                destination TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                prompt_message_id INTEGER
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS queue_jobs (
                job_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                telegram_chat_id INTEGER NOT NULL,
                telegram_message_id INTEGER NOT NULL,
                telegram_file_id TEXT,
                image_path TEXT NOT NULL,
                job_json_path TEXT NOT NULL,
                status TEXT NOT NULL,
                destination TEXT,
                vision_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES batches(batch_id)
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_queue_jobs_status_created
            ON queue_jobs(status, created_at)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_queue_jobs_batch
            ON queue_jobs(batch_id, created_at)
            """
        )

        # A process crash during Vision must not strand a photo forever.
        db.execute(
            """
            UPDATE queue_jobs
            SET status = 'queued',
                updated_at = ?
            WHERE status = 'processing_vision'
            """,
            (_utc_now_iso(),),
        )


def _db_get_collecting_batch(user_id, chat_id):
    with _db_connect() as db:
        row = db.execute(
            """
            SELECT *
            FROM batches
            WHERE telegram_user_id = ?
              AND telegram_chat_id = ?
              AND state = 'collecting'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (int(user_id), int(chat_id)),
        ).fetchone()
    return dict(row) if row else None


def _db_create_batch(user_id, chat_id):
    batch_id = uuid.uuid4().hex[:16]
    now = _utc_now_iso()
    with _db_connect() as db:
        db.execute(
            """
            INSERT INTO batches (
                batch_id,
                telegram_user_id,
                telegram_chat_id,
                state,
                destination,
                created_at,
                updated_at,
                prompt_message_id
            ) VALUES (?, ?, ?, 'collecting', NULL, ?, ?, NULL)
            """,
            (batch_id, int(user_id), int(chat_id), now, now),
        )
    return batch_id


def _db_touch_batch(batch_id):
    with _db_connect() as db:
        db.execute(
            """
            UPDATE batches
            SET updated_at = ?
            WHERE batch_id = ?
            """,
            (_utc_now_iso(), batch_id),
        )


def _db_get_batch(batch_id):
    with _db_connect() as db:
        row = db.execute(
            "SELECT * FROM batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    return dict(row) if row else None


def _db_count_batch_jobs(batch_id):
    with _db_connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS count FROM queue_jobs WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
    return int(row["count"] if row else 0)


def _db_insert_queue_job(job):
    now = _utc_now_iso()
    with _db_connect() as db:
        db.execute(
            """
            INSERT INTO queue_jobs (
                job_id,
                batch_id,
                telegram_user_id,
                telegram_chat_id,
                telegram_message_id,
                telegram_file_id,
                image_path,
                job_json_path,
                status,
                destination,
                vision_attempts,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                job["jobId"],
                job["batchId"],
                int(job["telegramUserId"]),
                int(job["telegramChatId"]),
                int(job["telegramMessageId"]),
                job.get("telegramFileId"),
                job["imagePath"],
                job["jobPath"],
                job["status"],
                job.get("destination"),
                now,
                now,
            ),
        )


def _db_update_queue_job(job_id, **changes):
    allowed = {
        "status",
        "destination",
        "vision_attempts",
        "image_path",
        "job_json_path",
    }

    pairs = []
    values = []

    for key, value in changes.items():
        if key not in allowed:
            continue
        pairs.append(f"{key} = ?")
        values.append(value)

    pairs.append("updated_at = ?")
    values.append(_utc_now_iso())
    values.append(job_id)

    with _db_connect() as db:
        db.execute(
            f"UPDATE queue_jobs SET {', '.join(pairs)} WHERE job_id = ?",
            values,
        )


def _db_requeue_failed_vision_job(job_id):
    """
    Requeue one failed Vision job in the authoritative SQLite queue.

    JSON is only the persisted mirror; the worker claims from queue_jobs.
    If an older bug left the DB row stuck on processing_vision while the
    persisted Job is already vision_failed, manual Retry repairs that stale
    state and requeues it safely.
    """
    now = _utc_now_iso()

    with _db_connect() as db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            """
            SELECT job_id, batch_id, status
            FROM queue_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()

        if not row:
            db.execute("ROLLBACK")
            return {
                "ok": False,
                "reason": "queue_row_missing",
            }

        current_status = str(
            row["status"] or ""
        )

        if current_status == "queued":
            db.execute("COMMIT")
            return {
                "ok": True,
                "already_queued": True,
                "batch_id": row["batch_id"],
            }

        if current_status not in (
            "vision_failed",
            "processing_vision",
        ):
            db.execute("ROLLBACK")
            return {
                "ok": False,
                "reason": "queue_status_changed",
                "status": current_status,
            }

        cursor = db.execute(
            """
            UPDATE queue_jobs
            SET status = 'queued',
                updated_at = ?
            WHERE job_id = ?
              AND status = ?
            """,
            (
                now,
                job_id,
                current_status,
            ),
        )

        if cursor.rowcount != 1:
            db.execute("ROLLBACK")
            return {
                "ok": False,
                "reason": "queue_requeue_race",
            }

        db.execute(
            """
            UPDATE batches
            SET state = 'queued',
                updated_at = ?
            WHERE batch_id = ?
            """,
            (now, row["batch_id"]),
        )

        db.execute("COMMIT")

    return {
        "ok": True,
        "batch_id": row["batch_id"],
        "recovered_from": current_status,
    }


def _db_delete_queue_job(job_id):
    with _db_connect() as db:
        db.execute(
            "DELETE FROM queue_jobs WHERE job_id = ?",
            (job_id,),
        )


def _db_finalize_batch_waiting_destination(batch_id, prompt_message_id=None):
    with _db_connect() as db:
        db.execute(
            """
            UPDATE batches
            SET state = 'waiting_destination',
                prompt_message_id = ?,
                updated_at = ?
            WHERE batch_id = ?
              AND state IN ('collecting', 'waiting_destination')
              AND prompt_message_id IS NULL
            """,
            (prompt_message_id, _utc_now_iso(), batch_id),
        )


def _db_assign_batch_destination(batch_id, destination):
    now = _utc_now_iso()
    with _db_connect() as db:
        db.execute("BEGIN IMMEDIATE")

        batch = db.execute(
            "SELECT * FROM batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()

        if not batch:
            db.execute("ROLLBACK")
            return None

        if batch["state"] not in ("waiting_destination", "collecting"):
            db.execute("ROLLBACK")
            return dict(batch)

        db.execute(
            """
            UPDATE batches
            SET state = 'queued',
                destination = ?,
                updated_at = ?
            WHERE batch_id = ?
            """,
            (destination, now, batch_id),
        )

        db.execute(
            """
            UPDATE queue_jobs
            SET status = 'queued',
                destination = ?,
                updated_at = ?
            WHERE batch_id = ?
              AND status IN ('waiting_destination', 'received')
            """,
            (destination, now, batch_id),
        )

        db.execute("COMMIT")

    return _db_get_batch(batch_id)


def _db_claim_next_job():
    with _db_connect() as db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            """
            SELECT *
            FROM queue_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()

        if not row:
            db.execute("COMMIT")
            return None

        attempts = int(row["vision_attempts"] or 0) + 1

        db.execute(
            """
            UPDATE queue_jobs
            SET status = 'processing_vision',
                vision_attempts = ?,
                updated_at = ?
            WHERE job_id = ?
              AND status = 'queued'
            """,
            (attempts, _utc_now_iso(), row["job_id"]),
        )

        db.execute("COMMIT")
    claimed = dict(row)
    claimed["vision_attempts"] = attempts
    claimed["status"] = "processing_vision"
    return claimed


def _db_mark_batch_done_if_complete(batch_id):
    with _db_connect() as db:
        remaining = db.execute(
            """
            SELECT COUNT(*) AS count
            FROM queue_jobs
            WHERE batch_id = ?
              AND status NOT IN (
                  'completed',
                  'completed_duplicate',
                  'sheet_submitted',
                  'sheet_failed',
                  'sheet_unconfirmed',
                  'vision_failed'
              )
            """,
            (batch_id,),
        ).fetchone()

        if int(remaining["count"] if remaining else 0) == 0:
            db.execute(
                """
                UPDATE batches
                SET state = 'finished',
                    updated_at = ?
                WHERE batch_id = ?
                """,
                (_utc_now_iso(), batch_id),
            )


async def _safe_bot_send(bot, chat_id, text, reply_markup=None):
    try:
        await asyncio.wait_for(
            bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            ),
            timeout=TELEGRAM_STATUS_MESSAGE_TIMEOUT_SECONDS,
        )
        return True
    except (NetworkError, TimedOut) as ex:
        print("")
        print(
            "TELEGRAM BOT SEND NETWORK WARNING:",
            type(ex).__name__,
            str(ex),
        )
        print("")
        return False
    except Exception:
        print("")
        print("TELEGRAM BOT SEND FAILED:")
        traceback.print_exc()
        print("")
        return False


def _db_get_incomplete_download_jobs():
    with _db_connect() as db:
        rows = db.execute(
            """
            SELECT *
            FROM queue_jobs
            WHERE status IN ('receiving', 'receive_failed')
            ORDER BY created_at ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _db_get_batches_needing_prompt():
    with _db_connect() as db:
        rows = db.execute(
            """
            SELECT b.*
            FROM batches b
            WHERE (
                    b.state = 'collecting'
                    OR (
                        b.state = 'waiting_destination'
                        AND b.prompt_message_id IS NULL
                    )
                  )
              AND EXISTS (
                  SELECT 1
                  FROM queue_jobs q
                  WHERE q.batch_id = b.batch_id
                    AND q.status = 'waiting_destination'
              )
            ORDER BY b.created_at ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


async def _recover_incomplete_downloads(application):
    jobs = await asyncio.to_thread(
        _db_get_incomplete_download_jobs
    )

    for job in jobs:
        job_id = job["job_id"]
        image_path = job["image_path"]
        job_path = job["job_json_path"]
        telegram_file_id = job.get("telegram_file_id")
        chat_id = int(job["telegram_chat_id"])

        try:
            if not os.path.exists(image_path):
                if not telegram_file_id:
                    raise RuntimeError("Telegram file_id is missing.")

                telegram_file = await application.bot.get_file(
                    telegram_file_id
                )
                await telegram_file.download_to_drive(
                    custom_path=image_path
                )

            if os.path.exists(job_path):
                update_persisted_job(
                    job_path,
                    status="waiting_destination",
                    lastError=None,
                )
            else:
                _db_update_queue_job(
                    job_id,
                    status="waiting_destination",
                )

            print("RECOVERED TELEGRAM IMAGE:", job_id)

        except Exception as ex:
            _db_update_queue_job(
                job_id,
                status="receive_failed",
            )
            if os.path.exists(job_path):
                try:
                    update_persisted_job(
                        job_path,
                        status="receive_failed",
                        lastError=str(ex),
                    )
                except Exception:
                    pass

            await _safe_bot_send(
                application.bot,
                chat_id,
                "⚠️ بازیابی یکی از تصاویر دریافت‌شده کامل نشد.\n"
                f"Job ID: {job_id}",
            )


async def _recover_destination_prompts(application):
    batches = await asyncio.to_thread(
        _db_get_batches_needing_prompt
    )

    for batch in batches:
        batch_id = batch["batch_id"]

        # A batch that already has a destination prompt must never be
        # prompted again during restart recovery.
        if batch.get("prompt_message_id") is not None:
            continue

        count = await asyncio.to_thread(
            _db_count_batch_jobs,
            batch_id,
        )

        if count <= 0:
            continue

        try:
            message = await application.bot.send_message(
                chat_id=int(batch["telegram_chat_id"]),
                text=(
                    f"♻️ {count} عکس محفوظ از قبل پیدا شد.\n\n"
                    "مقصد این Batch را انتخاب کنید:"
                ),
                reply_markup=_destination_keyboard(batch_id),
            )

            _db_finalize_batch_waiting_destination(
                batch_id,
                prompt_message_id=message.message_id,
            )

        except Exception:
            print("DESTINATION PROMPT RECOVERY FAILED:", batch_id)
            traceback.print_exc()


def _destination_keyboard(batch_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="📅 همایش آذرماه",
                    callback_data=f"dest:{DESTINATION_AZAR_EVENT}:{batch_id}",
                ),
                InlineKeyboardButton(
                    text="🎓 فروش دوره مذاکره",
                    callback_data=f"dest:{DESTINATION_COURSE}:{batch_id}",
                ),
            ]
        ]
    )


async def _finalize_batch_after_quiet_period(application, user_id, batch_id):
    try:
        await asyncio.sleep(BATCH_COLLECT_SECONDS)

        batch = _db_get_batch(batch_id)
        if not batch or batch.get("state") != "collecting":
            return

        count = _db_count_batch_jobs(batch_id)
        if count <= 0:
            return

        label = "عکس" if count == 1 else "عکس"
        message = await application.bot.send_message(
            chat_id=int(batch["telegram_chat_id"]),
            text=(
                f"✅ {count} {label} با موفقیت دریافت و محفوظ شد.\n\n"
                "این اطلاعات برای کدام بخش است؟"
            ),
            reply_markup=_destination_keyboard(batch_id),
        )

        _db_finalize_batch_waiting_destination(
            batch_id,
            prompt_message_id=message.message_id,
        )

    except asyncio.CancelledError:
        raise
    except Exception:
        print("")
        print("BATCH FINALIZE ERROR:")
        traceback.print_exc()
        print("")
    finally:
        current = batch_finalize_tasks.get(user_id)
        if current is asyncio.current_task():
            batch_finalize_tasks.pop(user_id, None)


def _schedule_batch_finalize(application, user_id, batch_id):
    old_task = batch_finalize_tasks.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()

    task = application.create_task(
        _finalize_batch_after_quiet_period(
            application,
            user_id,
            batch_id,
        )
    )
    batch_finalize_tasks[user_id] = task


# =========================================================
# END PERSISTENT BATCH + VISION QUEUE
# =========================================================

# =========================================================
# JOB PERSISTENCE + SAFE TELEGRAM STATUS
# =========================================================


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _gregorian_to_jalali(gy, gm, gd):
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

    gy -= 1600
    gm -= 1
    gd -= 1

    g_day_no = (
        365 * gy
        + (gy + 3) // 4
        - (gy + 99) // 100
        + (gy + 399) // 400
    )

    for i in range(gm):
        g_day_no += g_days_in_month[i]

    if (
        gm > 1
        and (
            (gy % 4 == 0 and gy % 100 != 0)
            or (gy % 400 == 0)
        )
    ):
        g_day_no += 1

    g_day_no += gd

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    jm = 0
    while (
        jm < 11
        and j_day_no >= j_days_in_month[jm]
    ):
        j_day_no -= j_days_in_month[jm]
        jm += 1

    jd = j_day_no + 1

    return jy, jm + 1, jd


def _format_jalali_datetime(iso_value):
    text = str(
        iso_value or ""
    ).strip()

    if not text:
        return ""

    try:
        dt = datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00"
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        iran_tz = timezone(
            timedelta(
                hours=3,
                minutes=30
            )
        )

        dt = dt.astimezone(
            iran_tz
        )

        jy, jm, jd = _gregorian_to_jalali(
            dt.year,
            dt.month,
            dt.day
        )

        return (
            f"{jy:04d}/{jm:02d}/{jd:02d} "
            f"{dt.hour:02d}:{dt.minute:02d}"
        )

    except Exception:
        return text


def _write_json_atomic(path, payload):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def create_received_job(
    update: Update,
    batch_id,
    telegram_file_id,
    image_path,
):
    user_id = get_telegram_user_id(update) or 0
    message_id = (update.message.message_id if update.message else 0)
    chat_id = (update.effective_chat.id if update.effective_chat else 0)
    job_id = f"{user_id}_{message_id}_{uuid.uuid4().hex[:8]}"
    job_path = os.path.join(JOBS_DIR, job_id + ".json")

    job = {
        "jobId": job_id,
        "batchId": batch_id,
        "status": "waiting_destination",
        "destination": None,
        "createdAt": _utc_now_iso(),
        "updatedAt": _utc_now_iso(),
        "telegramUserId": user_id,
        "telegramChatId": chat_id,
        "telegramMessageId": message_id,
        "telegramFileId": telegram_file_id,
        "imagePath": image_path,
        "data": None,
        "requestId": None,
        "googleResponse": None,
        "lastError": None,
        "visionAttempts": 0,
    }

    _write_json_atomic(job_path, job)

    _db_insert_queue_job(
        {
            **job,
            "jobPath": job_path,
        }
    )

    return job_path, job


def create_persisted_job(update: Update, data, source_image_path):
    """Backward-compatible helper for legacy call sites/files."""
    user_id = get_telegram_user_id(update) or 0
    message_id = (update.message.message_id if update.message else 0)
    job_id = f"{user_id}_{message_id}_{uuid.uuid4().hex[:8]}"

    image_ext = os.path.splitext(source_image_path)[1] or ".jpg"
    image_path = os.path.join(JOBS_DIR, job_id + image_ext)
    job_path = os.path.join(JOBS_DIR, job_id + ".json")

    shutil.copy2(source_image_path, image_path)

    job = {
        "jobId": job_id,
        "status": "vision_done",
        "createdAt": _utc_now_iso(),
        "updatedAt": _utc_now_iso(),
        "telegramUserId": user_id,
        "telegramChatId": (update.effective_chat.id if update.effective_chat else None),
        "telegramMessageId": message_id,
        "imagePath": image_path,
        "data": data,
        "googleResponse": None,
        "lastError": None,
    }

    _write_json_atomic(job_path, job)
    return job_path, job


def update_persisted_job(job_path, **changes):
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)

    job.update(changes)
    job["updatedAt"] = _utc_now_iso()
    _write_json_atomic(job_path, job)

    job_id = str(job.get("jobId") or "").strip()
    if job_id:
        db_changes = {}
        if "status" in changes:
            db_changes["status"] = changes["status"]
        if "destination" in changes:
            db_changes["destination"] = changes["destination"]
        if "visionAttempts" in changes:
            db_changes["vision_attempts"] = changes["visionAttempts"]

        if db_changes:
            try:
                _db_update_queue_job(job_id, **db_changes)
            except sqlite3.Error:
                # Legacy jobs may not exist in the queue DB. Their JSON retry path
                # must continue to work exactly as before.
                pass

    return job


async def safe_reply(
    update: Update,
    text,
    timeout_seconds=TELEGRAM_STATUS_MESSAGE_TIMEOUT_SECONDS
):
    if not update.message:
        return False

    try:
        await asyncio.wait_for(
            update.message.reply_text(text),
            timeout=timeout_seconds,
        )
        return True
    except (NetworkError, TimedOut) as ex:
        print("")
        print(
            "TELEGRAM STATUS MESSAGE NETWORK WARNING:",
            type(ex).__name__,
            str(ex)
        )
        print("")
        return False

    except Exception:
        print("")
        print("TELEGRAM STATUS MESSAGE FAILED:")
        traceback.print_exc()
        print("")
        return False


# =========================================================
# GOOGLE SHEET
# =========================================================

# =========================================================
# START FUNCTION: send_to_google_sheet_sync
# =========================================================
def send_to_google_sheet_sync(
    data,
    image_path=None,
    request_id=None,
    status_only=False,
    destination=DESTINATION_COURSE
):

    # =========================================================
    # 1) REQUEST ID
    # =========================================================

    request_id = (request_id or uuid.uuid4().hex).strip()

    # =========================================================
    # 2) PAYLOAD
    # =========================================================

    payload = {
        "requestId": request_id,
        "secret": get_google_sheet_secret(destination),
        "instagramId": data.get("instagramId", ""),
        "fullName": data.get("fullName", ""),
        "phoneNumber": data.get("phoneNumber", ""),
        "job": data.get("job", ""),
        "employeeCount": data.get("employeeCount", ""),
        "city": data.get("city", ""),
        "challenge": data.get("challenge", ""),
        "description": data.get("description", ""),
    }

    # =========================================================
    # 3) IMAGE -> BASE64
    # =========================================================

    if image_path and os.path.exists(image_path):

        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()

        payload["imageBase64"] = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        payload["imageFileName"] = os.path.basename(
            image_path
        )

        mime_type, _ = mimetypes.guess_type(
            image_path
        )

        payload["imageMimeType"] = (
            mime_type or "image/jpeg"
        )

    # =========================================================
    # 4) GOOGLE APPS SCRIPT URL
    # =========================================================

    google_url = get_google_sheet_url(destination).strip()

    if not google_url:
        raise RuntimeError(
            "Google Sheet URL is empty."
        )

    if not google_url.endswith("/exec"):
        raise RuntimeError(
            "Google Apps Script URL must end with /exec. "
            f"Current URL: {google_url}"
        )

    # =========================================================
    # 5) BODY + HEADERS
    # =========================================================

    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":")
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "NegotiateCourseTelegramBot/5.0",
    }

    # =========================================================
    # 6) STATUS LOOKUP
    #
    # IMPORTANT:
    # Each status check starts from the stable /exec URL.
    # We do not reuse a previous redirected googleusercontent URL.
    # =========================================================

    def get_request_status():

        response = requests.get(
            google_url,
            params={
                "action": "status",
                "requestId": request_id,
                "secret": get_google_sheet_secret(destination),
                "_cb": uuid.uuid4().hex,
            },
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
                "Connection": "close",
                "User-Agent": "NegotiateCourseTelegramBot/5.0",
            },
            timeout=(
                GOOGLE_STATUS_CONNECT_TIMEOUT_SECONDS,
                GOOGLE_STATUS_READ_TIMEOUT_SECONDS,
            ),
            allow_redirects=True,
        )

        print("")
        print("========== GOOGLE STATUS =========")
        print("REQUEST ID:", request_id)
        print("STATUS:", response.status_code)
        print("URL:", response.url)
        print("BODY:", response.text[:2500])
        print("==================================")
        print("")

        if response.status_code == 404:
            raise requests.RequestException(
                "Temporary Google status redirect returned 404."
            )

        response.raise_for_status()

        try:
            status_result = response.json()
        except Exception as ex:
            raise RuntimeError(
                "Google status response is not valid JSON:\n"
                + response.text[:2000]
            ) from ex

        if not isinstance(status_result, dict):
            raise RuntimeError(
                "Google status response has invalid JSON type."
            )

        if status_result.get("error") == "UNAUTHORIZED":
            raise RuntimeError(
                "Google status endpoint returned UNAUTHORIZED."
            )

        return status_result

    # =========================================================
    # 7) POLL SAVED RESULT
    #
    # Returns:
    #   dict -> confirmed result
    #   None -> still unconfirmed
    #
    # not_found is NOT considered a failure by itself.
    # =========================================================

    def poll_saved_result(
        attempts=GOOGLE_STATUS_POLL_ATTEMPTS,
        delay_seconds=GOOGLE_STATUS_POLL_DELAY_SECONDS
    ):

        last_status = None

        if GOOGLE_STATUS_INITIAL_DELAY_SECONDS > 0:
            time.sleep(
                GOOGLE_STATUS_INITIAL_DELAY_SECONDS
            )

        for attempt in range(1, attempts + 1):

            try:
                last_status = get_request_status()

            except requests.RequestException as ex:
                print(
                    f"GOOGLE STATUS NETWORK ERROR "
                    f"({attempt}/{attempts}): {ex}"
                )
                last_status = None

            if isinstance(last_status, dict):

                if last_status.get("requestStatus") == "done":
                    return last_status

                if (
                    last_status.get("found") is True
                    and last_status.get("status") == "processing"
                ):
                    pass

                elif (
                    last_status.get("found") is False
                    and last_status.get("status") == "not_found"
                ):
                    pass

                elif (
                    "success" in last_status
                    and last_status.get("status") not in (
                        "processing",
                        "not_found",
                    )
                ):
                    return last_status

            if attempt < attempts:
                time.sleep(
                    delay_seconds
                )

        return None

    # =========================================================
    # 8) STATUS-ONLY MODE
    #
    # /retry uses this FIRST.
    # It never POSTs in this mode.
    # =========================================================

    if status_only:

        status_result = poll_saved_result()

        if status_result is not None:
            status_result["requestId"] = request_id
            return status_result

        return {
            "success": False,
            "requestId": request_id,
            "requestStatus": "unconfirmed",
            "statusOnly": True,
        }

    post_redirect_received = False

    # =========================================================
    # 9) POST ONCE
    #
    # 302 means Apps Script accepted the request and returned
    # the normal ContentService redirect.
    # We DO NOT follow that POST redirect.
    # =========================================================

    def post_once():
        nonlocal post_redirect_received

        try:
            response = requests.post(
                google_url,
                data=body,
                headers=headers,
                timeout=(
                    GOOGLE_POST_CONNECT_TIMEOUT_SECONDS,
                    GOOGLE_POST_READ_TIMEOUT_SECONDS,
                ),
                allow_redirects=False,
            )

            print("")
            print("========== GOOGLE POST ==========")
            print("REQUEST ID:", request_id)
            print("STATUS:", response.status_code)
            print("URL:", response.url)
            print(
                "LOCATION:",
                response.headers.get("Location", "")
            )
            print("BODY:", response.text[:1500])
            print("=================================")
            print("")

            if 200 <= response.status_code < 300:

                raw_text = response.text.strip()

                if raw_text:
                    try:
                        direct_result = response.json()
                    except Exception:
                        direct_result = None

                    if isinstance(direct_result, dict):

                        if (
                            direct_result.get("status") == "online"
                            and "row" not in direct_result
                            and "duplicate" not in direct_result
                        ):
                            direct_result = None

                        if direct_result is not None:
                            return direct_result

            if response.status_code in (
                301,
                302,
                303,
                307,
                308,
            ):
                post_redirect_received = True
                return None

            response.raise_for_status()
            return None

        except requests.Timeout as ex:
            print("")
            print("GOOGLE POST TIMEOUT - CHECKING REQUEST STATUS")
            print("REQUEST ID:", request_id)
            print("ERROR:", str(ex))
            print("")
            return None

        except requests.RequestException as ex:
            print("")
            print("GOOGLE POST NETWORK ERROR - CHECKING REQUEST STATUS")
            print("REQUEST ID:", request_id)
            print("ERROR:", str(ex))
            print("")
            return None

    # =========================================================
    # 10) FIRST POST + STATUS CONFIRMATION
    # =========================================================

    direct_result = post_once()

    if isinstance(direct_result, dict):
        result = direct_result
    else:
        result = poll_saved_result()

    # =========================================================
    # 11) OPTIONAL SAFE AUTO-POST RETRY
    # =========================================================

    for auto_retry_index in range(
        GOOGLE_AUTO_POST_RETRY_COUNT
    ):

        if result is not None:
            break

        print("")
        print(
            "GOOGLE RESULT NOT CONFIRMED. "
            f"AUTO RETRY {auto_retry_index + 1}/"
            f"{GOOGLE_AUTO_POST_RETRY_COUNT}"
        )
        print("REQUEST ID:", request_id)
        print("")

        direct_result = post_once()

        if isinstance(direct_result, dict):
            result = direct_result
        else:
            result = poll_saved_result()

    # =========================================================
    # 12) FINAL VALIDATION
    # =========================================================

    if result is None:

        if post_redirect_received:
            return {
                "success": True,
                "submitted": True,
                "requestId": request_id,
                "requestStatus": "submitted_unconfirmed",
                "message": (
                    "Google Apps Script accepted the POST, but the final "
                    "result could not be read from the status endpoint."
                ),
            }

        raise RuntimeError(
            "Google Sheet request could not be confirmed and no "
            "Apps Script redirect was received. "
            f"requestId={request_id}. "
            "Vision data is safely stored locally."
        )

    if not isinstance(result, dict):
        raise RuntimeError(
            "Google Sheet returned invalid response type."
        )

    if "success" not in result:
        raise RuntimeError(
            "Google Sheet response does not contain 'success': "
            + json.dumps(
                result,
                ensure_ascii=False
            )
        )

    result["requestId"] = request_id

    print("")
    print("GOOGLE APPS SCRIPT RESULT:")
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )
    print("")

    return result


# =========================================================
# END FUNCTION: send_to_google_sheet_sync
# =========================================================

def test_google_sheet_sync(destination=DESTINATION_COURSE):

    response = requests.get(
        get_google_sheet_url(destination),
        timeout=20
    )

    response.raise_for_status()

    try:
        return response.json()

    except Exception:
        return {
            "success": False,
            "error": "INVALID_JSON",
            "raw": response.text
        }



# =========================================================
# START RETRY HELPERS
# =========================================================

RETRYABLE_JOB_STATUSES = {
    "vision_failed",
    "vision_done",
    "sheet_pending",
    "sheet_unconfirmed",
    "sheet_failed",
    "failed_after_vision",
}


def _load_job_file(job_path):
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)

    if not isinstance(job, dict):
        raise ValueError("Invalid job JSON.")

    return job


def _job_sort_key(job):
    return (
        str(job.get("updatedAt") or ""),
        str(job.get("createdAt") or ""),
        str(job.get("jobId") or ""),
    )


def _retry_to_english_digits(value):
    text = str(value or "")

    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"

    for index, digit in enumerate(persian):
        text = text.replace(digit, str(index))

    for index, digit in enumerate(arabic):
        text = text.replace(digit, str(index))

    return text


def _normalize_retry_instagram(value):
    text = _retry_to_english_digits(
        value
    ).strip().lower()

    if not text:
        return ""

    text = re.sub(
        r"^https?://(www\\.)?instagram\\.com/",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^www\\.instagram\\.com/",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^@+",
        "",
        text
    )

    text = text.split("?")[0]
    text = text.split("#")[0]
    text = text.strip("/").strip()

    return text


def _normalize_retry_phone(value):
    text = _retry_to_english_digits(
        value
    )

    phone = re.sub(
        r"\\D",
        "",
        text
    )

    if not phone:
        return ""

    if phone.startswith("0098"):
        phone = "0" + phone[4:]

    elif (
        phone.startswith("98")
        and len(phone) >= 12
    ):
        phone = "0" + phone[2:]

    elif (
        len(phone) == 10
        and phone.startswith("9")
    ):
        phone = "0" + phone

    return phone


def _retry_identity_values(job):
    data = job.get("data") or {}

    instagram_id = _normalize_retry_instagram(
        data.get("instagramId")
    )

    phone = _normalize_retry_phone(
        data.get("phoneNumber")
    )

    return instagram_id, phone


def _retry_image_identity(job):
    image_path = str(
        job.get("imagePath") or ""
    ).strip()

    if image_path and os.path.exists(image_path):
        try:
            digest = hashlib.sha256()

            with open(image_path, "rb") as image_file:
                for chunk in iter(
                    lambda: image_file.read(1024 * 1024),
                    b""
                ):
                    digest.update(chunk)

            return "sha256:" + digest.hexdigest()
        except Exception:
            pass

    telegram_file_id = str(
        job.get("telegramFileId") or ""
    ).strip()

    if telegram_file_id:
        return "telegram:" + telegram_file_id

    return ""


def get_retryable_jobs_for_allowed_users():
    jobs = []

    if not os.path.isdir(JOBS_DIR):
        return jobs

    for name in os.listdir(JOBS_DIR):

        if not name.lower().endswith(".json"):
            continue

        job_path = os.path.join(JOBS_DIR, name)

        try:
            job = _load_job_file(job_path)
        except Exception:
            print("")
            print("SKIPPING INVALID JOB FILE:", job_path)
            traceback.print_exc()
            print("")
            continue

        telegram_user_id = int(
            job.get("telegramUserId") or 0
        )

        if telegram_user_id not in ALLOWED_USER_IDS:
            continue

        if job.get("status") not in RETRYABLE_JOB_STATUSES:
            continue

        job["_jobPath"] = job_path
        jobs.append(job)

    # Newest first.
    jobs.sort(
        key=_job_sort_key,
        reverse=True
    )

    # =========================================================
    # DEDUPE RETRY LIST
    #
    # 1) After Vision we can dedupe by Instagram / phone.
    # 2) Before Vision succeeds there is no name/phone yet, so use the
    #    persisted image SHA-256. This also works when Telegram gives the
    #    same picture a different message id or file id.
    # =========================================================

    deduped_jobs = []
    seen_instagrams = set()
    seen_phones = set()
    seen_images = set()

    for job in jobs:

        instagram_id, phone = _retry_identity_values(
            job
        )

        image_identity = _retry_image_identity(
            job
        )

        duplicate_lead = False

        if (
            instagram_id
            and instagram_id in seen_instagrams
        ):
            duplicate_lead = True

        if (
            phone
            and phone in seen_phones
        ):
            duplicate_lead = True

        if (
            image_identity
            and image_identity in seen_images
        ):
            duplicate_lead = True

        if duplicate_lead:
            continue

        deduped_jobs.append(
            job
        )

        if instagram_id:
            seen_instagrams.add(
                instagram_id
            )

        if phone:
            seen_phones.add(
                phone
            )

        if image_identity:
            seen_images.add(
                image_identity
            )

    # Attach transparent duplicate metadata to every visible retry item.
    # The UI stays compact (one representative per group), but the operator
    # can still see exactly which Job IDs were hidden by deduplication.
    for representative in deduped_jobs:

        representative_job_id = str(
            representative.get("jobId") or ""
        ).strip()

        hidden_job_ids = []

        try:
            group_jobs = _get_retry_group_jobs(
                representative
            )
        except Exception:
            group_jobs = []

        for grouped_job in group_jobs:

            grouped_job_id = str(
                grouped_job.get("jobId") or ""
            ).strip()

            if (
                grouped_job_id
                and grouped_job_id != representative_job_id
            ):
                hidden_job_ids.append(
                    grouped_job_id
                )

        # Newest visible representative remains first; hidden ids are shown
        # newest-first as returned by the filesystem/group scan is not stable,
        # so sort by their persisted job metadata where possible.
        hidden_job_ids = list(
            dict.fromkeys(
                hidden_job_ids
            )
        )

        representative["_hiddenDuplicateJobIds"] = hidden_job_ids
        representative["_hiddenDuplicateCount"] = len(
            hidden_job_ids
        )

    return deduped_jobs


def _get_retry_group_jobs(selected_job):
    """
    Return all retryable jobs representing the same item.

    Exact image identity is preferred because Vision-failed jobs do not yet
    have a reliable extracted lead identity. If no image identity exists,
    fall back to normalized Instagram/phone.
    """
    selected_image = _retry_image_identity(
        selected_job
    )

    selected_instagram, selected_phone = _retry_identity_values(
        selected_job
    )

    matches = []

    if not os.path.isdir(JOBS_DIR):
        return matches

    for name in os.listdir(JOBS_DIR):

        if not name.lower().endswith(".json"):
            continue

        job_path = os.path.join(
            JOBS_DIR,
            name
        )

        try:
            job = _load_job_file(
                job_path
            )
        except Exception:
            continue

        telegram_user_id = int(
            job.get("telegramUserId") or 0
        )

        if telegram_user_id not in ALLOWED_USER_IDS:
            continue

        if job.get("status") not in RETRYABLE_JOB_STATUSES:
            continue

        same_item = False

        candidate_image = _retry_image_identity(
            job
        )

        if (
            selected_image
            and candidate_image
            and selected_image == candidate_image
        ):
            same_item = True

        if not same_item:
            candidate_instagram, candidate_phone = _retry_identity_values(
                job
            )

            if (
                selected_instagram
                and candidate_instagram == selected_instagram
            ):
                same_item = True

            if (
                selected_phone
                and candidate_phone == selected_phone
            ):
                same_item = True

        if same_item:
            job["_jobPath"] = job_path
            matches.append(job)

    # If no identity was available, the selected job itself is still valid.
    if not matches:
        selected_copy = dict(
            selected_job
        )
        selected_copy["_jobPath"] = os.path.join(
            JOBS_DIR,
            f"{selected_job.get('jobId')}.json"
        )
        matches.append(
            selected_copy
        )

    return matches


def _retry_status_label(status):
    labels = {
        "vision_failed": "نیاز به تلاش مجدد برای استخراج اطلاعات",
        "vision_done": "اطلاعات استخراج شده؛ ارسال نهایی نشده",
        "sheet_pending": "در انتظار نتیجه ثبت",
        "sheet_unconfirmed": "نیاز به بررسی وضعیت ثبت",
        "sheet_failed": "ارسال به شیت ناموفق",
        "failed_after_vision": "استخراج انجام شده؛ ثبت نهایی نشده",
    }

    return labels.get(
        str(status or "").strip(),
        "نیاز به بررسی"
    )


def _retry_job_label(job, number):
    data = job.get("data") or {}

    full_name = str(
        data.get("fullName") or ""
    ).strip()

    phone = str(
        data.get("phoneNumber") or ""
    ).strip()

    instagram_id = str(
        data.get("instagramId") or ""
    ).strip()

    status = str(
        job.get("status") or ""
    ).strip()

    telegram_user_id = str(
        job.get("telegramUserId") or ""
    ).strip()

    message_id = str(
        job.get("telegramMessageId") or ""
    ).strip()

    destination = str(
        job.get("destination") or ""
    ).strip()

    destination_label = DESTINATION_LABELS.get(
        destination,
        destination or "-"
    )

    created_at = str(
        job.get("createdAt") or ""
    ).strip()

    jalali_datetime = _format_jalali_datetime(
        created_at
    )

    job_id = str(
        job.get("jobId") or ""
    ).strip()

    identity = (
        full_name
        or phone
        or instagram_id
        or "اطلاعات استخراج نشده"
    )

    lines = [
        f"{number}) {identity}",
    ]

    if telegram_user_id:
        lines.append(
            f"   User: {telegram_user_id}"
        )

    if message_id:
        lines.append(
            f"   Msg: {message_id}"
        )

    if jalali_datetime:
        lines.append(
            f"   زمان: {jalali_datetime}"
        )

    lines.append(
        f"   مقصد: {destination_label}"
    )

    if job_id:
        lines.append(
            f"   Job: {job_id}"
        )

    hidden_duplicate_ids = list(
        job.get("_hiddenDuplicateJobIds") or []
    )

    hidden_duplicate_count = int(
        job.get("_hiddenDuplicateCount") or 0
    )

    if hidden_duplicate_count > 0:
        lines.append(
            f"   ♻️ تکراری‌های مخفی: {hidden_duplicate_count}"
        )

        max_visible_hidden_ids = 8

        for hidden_job_id in hidden_duplicate_ids[
            :max_visible_hidden_ids
        ]:
            lines.append(
                f"      └─ {hidden_job_id}"
            )

        remaining_hidden = (
            hidden_duplicate_count
            - max_visible_hidden_ids
        )

        if remaining_hidden > 0:
            lines.append(
                f"      └─ ... و {remaining_hidden} Job دیگر"
            )

    return "\n".join(
        lines
    )


def _build_retry_keyboard(jobs):
    rows = []

    for index, job in enumerate(jobs, start=1):

        job_id = str(
            job.get("jobId") or ""
        ).strip()

        if not job_id:
            continue

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔄 Retry {index}",
                    callback_data=f"retryjob:{job_id}"
                ),
                InlineKeyboardButton(
                    text=f"🗑 حذف کامل {index}",
                    callback_data=f"retrydelete:{job_id}"
                )
            ]
        )

    return InlineKeyboardMarkup(rows)


async def _safe_callback_message(query, text, reply_markup=None):
    try:
        await asyncio.wait_for(
            query.message.reply_text(
                text,
                reply_markup=reply_markup
            ),
            timeout=8
        )
        return True
    except (NetworkError, TimedOut) as ex:
        print("")
        print(
            "TELEGRAM CALLBACK MESSAGE NETWORK WARNING:",
            type(ex).__name__,
            str(ex)
        )
        print("")
        return False

    except Exception:
        print("")
        print("TELEGRAM CALLBACK MESSAGE FAILED:")
        traceback.print_exc()
        print("")
        return False


def _duplicate_message_from_response(data, google_response):
    duplicate_by = google_response.get(
        "duplicateBy",
        ""
    )

    duplicate_row = google_response.get(
        "row",
        ""
    )

    duplicate_value = google_response.get(
        "value",
        ""
    )

    if duplicate_by == "instagramId":

        duplicate_field = "آیدی اینستاگرام"

        if not duplicate_value:
            duplicate_value = data.get(
                "instagramId",
                ""
            )

    elif duplicate_by == "phoneNumber":

        duplicate_field = "شماره تماس"

        if not duplicate_value:
            duplicate_value = data.get(
                "phoneNumber",
                ""
            )

    else:
        duplicate_field = "اطلاعات مشابه"

    return (
        "⛔ رکورد تکراری است و دوباره ثبت نشد.\n\n"
        f"مورد تکراری: {duplicate_field}\n"
        f"مقدار: {duplicate_value or '-'}\n"
        f"ردیف قبلی: {duplicate_row or '-'}"
    )


async def retry_job_by_id(update, job_id, application=None):
    user_id = get_telegram_user_id(update)

    if not user_id:
        return

    job_path = os.path.join(
        JOBS_DIR,
        f"{job_id}.json"
    )

    if not os.path.exists(job_path):

        message = (
            "❌ این پردازش پیدا نشد یا فایل آن دیگر وجود ندارد.\n\n"
            f"Job ID: {job_id}"
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    try:
        job = _load_job_file(
            job_path
        )
    except Exception as ex:

        message = (
            "❌ فایل پردازش قابل خواندن نیست.\n\n"
            + str(ex)
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    if int(job.get("telegramUserId") or 0) not in ALLOWED_USER_IDS:

        message = (
            "⛔ مالک این پردازش در فهرست کاربران مجاز نیست."
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    current_status = job.get(
        "status",
        ""
    )

    if current_status == "retry_dismissed":

        message = (
            "ℹ️ این پردازش قبلاً از لیست Retry حذف شده است.\n\n"
            f"Job ID: {job_id}"
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    if current_status in (
        "completed",
        "completed_duplicate",
    ):

        message = (
            "ℹ️ این پردازش قبلاً نهایی شده و نیازی به Retry ندارد.\n\n"
            f"Job ID: {job_id}"
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    # =====================================================
    # MANUAL RETRY FOR A FAILED VISION JOB
    #
    # The original Telegram photo is already persisted. A user-requested
    # retry must reuse that exact image and put the same Job back on the
    # serial Vision queue; the user must not upload the photo again.
    # =====================================================

    if current_status == "vision_failed":

        image_path = job.get(
            "imagePath"
        )

        if not image_path or not os.path.exists(image_path):

            message = (
                "❌ تصویر ذخیره‌شده این پردازش پیدا نشد؛ "
                "Vision قابل Retry نیست.\n\n"
                f"Job ID: {job_id}"
            )

            if update.callback_query:
                await _safe_callback_message(
                    update.callback_query,
                    message
                )
            else:
                await safe_reply(
                    update,
                    message
                )

            return

        # First update the persisted mirror, then move the authoritative
        # SQLite queue row. If the DB requeue fails, restore the JSON status so
        # the UI never claims a Retry was queued when the worker cannot see it.
        update_persisted_job(
            job_path,
            status="queued",
            lastError=None,
        )

        try:
            requeue_result = await asyncio.to_thread(
                _db_requeue_failed_vision_job,
                job_id,
            )
        except Exception as ex:
            try:
                update_persisted_job(
                    job_path,
                    status="vision_failed",
                    lastError=(
                        "Manual Retry DB requeue failed: "
                        + str(ex)
                    ),
                )
            except Exception:
                pass

            print(
                "MANUAL RETRY QUEUE ERROR:",
                job_id,
                repr(ex),
            )

            message = (
                "❌ تلاش مجدد شروع نشد.\n"
                "عکس و اطلاعات این پردازش محفوظ مانده‌اند؛ لطفاً دوباره Retry را بزن.\n\n"
                f"Job ID: {job_id}"
            )

            if update.callback_query:
                await _safe_callback_message(
                    update.callback_query,
                    message
                )
            else:
                await safe_reply(
                    update,
                    message
                )

            return

        if not requeue_result.get("ok"):
            reason = str(
                requeue_result.get("reason") or "unknown"
            )

            current_db_status = str(
                requeue_result.get("status") or ""
            )

            rollback_error = (
                "Manual Retry was not queued in SQLite: "
                + reason
            )

            if current_db_status:
                rollback_error += (
                    f" (db_status={current_db_status})"
                )

            update_persisted_job(
                job_path,
                status="vision_failed",
                lastError=rollback_error,
            )

            print(
                "MANUAL RETRY REQUEUE REFUSED:",
                job_id,
                reason,
                current_db_status,
            )

            message = (
                "❌ تلاش مجدد فعلاً شروع نشد.\n"
                "عکس و اطلاعات این پردازش محفوظ مانده‌اند؛ "
                "لطفاً چند لحظه بعد دوباره Retry را بزن.\n\n"
                f"Job ID: {job_id}"
            )

            if update.callback_query:
                await _safe_callback_message(
                    update.callback_query,
                    message
                )
            else:
                await safe_reply(
                    update,
                    message
                )

            return

        if application is not None:
            await _ensure_vision_queue_worker(
                application
            )

        message = (
            "🔄 پردازش تصویر دوباره در صف قرار گرفت.\n"
            "📷 همان عکس ذخیره‌شده استفاده می‌شود و لازم نیست دوباره ارسالش کنی.\n\n"
            f"Job ID: {job_id}"
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    data = job.get(
        "data"
    ) or {}

    image_path = job.get(
        "imagePath"
    )

    request_id = (
        job.get("requestId")
        or job.get("jobId")
        or job_id
    )

    if not isinstance(data, dict) or not data:

        message = (
            "❌ JSON استخراج‌شده برای این پردازش وجود ندارد؛ "
            "Retry ایمن ممکن نیست."
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    if not image_path or not os.path.exists(image_path):

        message = (
            "❌ تصویر ذخیره‌شده این پردازش پیدا نشد؛ "
            "برای جلوگیری از ثبت ناقص Retry انجام نشد."
        )

        if update.callback_query:
            await _safe_callback_message(
                update.callback_query,
                message
            )
        else:
            await safe_reply(
                update,
                message
            )

        return

    status_message = (
        "🔎 در حال بررسی وضعیت ثبت این مورد در Google Sheet...\n"
        "ارسال مجدد انجام نمی‌شود مگر اینکه مطمئن باشیم ثبت قبلی انجام نشده است."
    )

    if update.callback_query:
        await _safe_callback_message(
            update.callback_query,
            status_message
        )
    else:
        await safe_reply(
            update,
            status_message
        )

    async with processing_lock:

        # =====================================================
        # A) STATUS-FIRST CHECK
        # =====================================================

        try:
            status_response = await asyncio.to_thread(
                send_to_google_sheet_sync,
                data,
                image_path,
                request_id,
                True,
                job.get("destination") or DESTINATION_COURSE
            )

        except Exception as ex:
            print("")
            print("RETRY STATUS-FIRST CHECK FAILED:")
            print("Job ID:", job_id)
            traceback.print_exc()
            print("")

            status_response = {
                "success": False,
                "requestStatus": "unconfirmed",
                "statusOnly": True,
                "error": str(ex),
            }

        # Already completed in Google -> DO NOT POST again.
        if status_response.get("requestStatus") == "done":

            google_response = status_response

        else:
            # =================================================
            # B) STILL UNCONFIRMED
            #
            # Safety rule:
            # - sheet_pending / sheet_unconfirmed may already have reached
            #   Apps Script, so DO NOT blindly POST them again.
            # - only states that never had a confirmed submission path are
            #   eligible for a new POST.
            # =================================================

            if current_status in (
                "sheet_pending",
                "sheet_unconfirmed",
                "sheet_submitted",
            ):
                update_persisted_job(
                    job_path,
                    status="sheet_unconfirmed",
                    requestId=request_id,
                    lastError="Status endpoint still unavailable."
                )
                _db_update_queue_job(
                    job_id,
                    status="sheet_unconfirmed",
                )

                message = (
                    "⚠️ هنوز نتوانستم وضعیت نهایی این مورد را از Google تأیید کنم.\n\n"
                    "ممکن است اطلاعات قبلاً در شیت ثبت شده باشد.\n"
                    "برای جلوگیری از ثبت تکراری، دوباره ارسالش نکردم.\n\n"
                    f"Job ID: {job_id}\n"
                    "می‌توانی بعداً دوباره /retry را برای بررسی وضعیت بزنی."
                )

                if update.callback_query:
                    await _safe_callback_message(
                        update.callback_query,
                        message
                    )
                else:
                    await safe_reply(
                        update,
                        message
                    )

                return

            # Only genuinely unsent/failed states reach this POST.
            try:
                update_persisted_job(
                    job_path,
                    status="sheet_pending",
                    requestId=request_id,
                    lastError=None
                )

                google_response = await asyncio.to_thread(
                    send_to_google_sheet_sync,
                    data,
                    image_path,
                    request_id,
                    False,
                    job.get("destination") or DESTINATION_COURSE
                )

            except Exception as ex:

                update_persisted_job(
                    job_path,
                    status="sheet_unconfirmed",
                    requestId=request_id,
                    lastError=str(ex)
                )
                _db_update_queue_job(
                    job_id,
                    status="sheet_unconfirmed",
                )

                print("")
                print("RETRY GOOGLE SHEET UNCONFIRMED:")
                print("Job ID:", job_id)
                traceback.print_exc()
                print("")

                message = (
                    "⚠️ نتیجه ثبت هنوز قابل تأیید نیست.\n\n"
                    "✅ اطلاعات استخراج‌شده محفوظ است.\n"
                    "🚫 Vision دوباره اجرا نشده است.\n"
                    f"Job ID: {job_id}"
                )

                if update.callback_query:
                    await _safe_callback_message(
                        update.callback_query,
                        message
                    )
                else:
                    await safe_reply(
                        update,
                        message
                    )

                return

        # =====================================================
        # C) FINALIZE RESULT
        # =====================================================

        if (
            google_response.get("requestStatus")
            == "submitted_unconfirmed"
        ):
            update_persisted_job(
                job_path,
                status="sheet_submitted",
                requestId=request_id,
                googleResponse=google_response,
                lastError=None
            )
            _db_update_queue_job(
                job_id,
                status="sheet_submitted",
            )

            message = (
                "✅ اطلاعات به Google تحویل شده است.\n"
                "تأیید نهایی هنوز قابل دریافت نیست.\n\n"
                "ممکن است رکورد در شیت ثبت شده باشد؛ "
                "برای جلوگیری از ثبت تکراری، دوباره ارسال نمی‌شود.\n"
                f"Job ID: {job_id}"
            )

        elif google_response.get("duplicate") is True:

            update_persisted_job(
                job_path,
                status="completed_duplicate",
                requestId=request_id,
                googleResponse=google_response,
                lastError=None
            )
            _db_update_queue_job(
                job_id,
                status="completed_duplicate",
            )

            message = _duplicate_message_from_response(
                data,
                google_response
            )

        elif google_response.get("success") is True:

            update_persisted_job(
                job_path,
                status="completed",
                requestId=request_id,
                googleResponse=google_response,
                lastError=None
            )
            _db_update_queue_job(
                job_id,
                status="completed",
            )

            row = google_response.get(
                "row",
                "-"
            )

            message = (
                "✅ بررسی شد: این مورد قبلاً در Google Sheet ثبت شده است.\n"
                "ارسال مجدد انجام نشد.\n\n"
                f"ردیف: {row}\n"
                f"Job ID: {job_id}"
            )

        else:

            error_message = (
                google_response.get("message")
                or google_response.get("error")
                or "خطای نامشخص"
            )

            update_persisted_job(
                job_path,
                status="sheet_failed",
                requestId=request_id,
                googleResponse=google_response,
                lastError=str(error_message)
            )
            _db_update_queue_job(
                job_id,
                status="sheet_failed",
            )

            message = (
                "⚠️ Google Sheet درخواست را نپذیرفت.\n\n"
                f"علت: {error_message}\n"
                f"Job ID: {job_id}\n\n"
                "✅ JSON استخراج‌شده همچنان محفوظ است."
            )

    if update.callback_query:
        await _safe_callback_message(
            update.callback_query,
            message
        )
    else:
        await safe_reply(
            update,
            message
        )


# =========================================================
# END RETRY HELPERS
# =========================================================


# =========================================================
# START COMMAND: /retry
# =========================================================

async def retry_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_allowed_user(update):
        await deny_access(update)
        return

    jobs = get_retryable_jobs_for_allowed_users()

    if not jobs:

        await safe_reply(
            update,
            "✅ هیچ پردازش ناموفق یا در انتظار Retry برای کاربران مجاز وجود ندارد."
        )
        return

    args = context.args

    if args:

        selection_text = str(
            args[0]
        ).strip()

        if not selection_text.isdigit():

            await safe_reply(
                update,
                "❌ شماره Retry معتبر نیست.\n\n"
                "مثال:\n"
                "/retry 1"
            )
            return

        selection = int(
            selection_text
        )

        if (
            selection < 1
            or selection > len(jobs)
        ):

            await safe_reply(
                update,
                "❌ چنین شماره‌ای در لیست Retry وجود ندارد.\n\n"
                f"شماره معتبر: 1 تا {len(jobs)}"
            )
            return

        selected_job = jobs[
            selection - 1
        ]

        await retry_job_by_id(
            update,
            selected_job["jobId"],
            context.application,
        )
        return

    lines = [
        f"⚠️ {len(jobs)} پردازش آماده Retry پیدا شد:",
        ""
    ]

    for index, job in enumerate(
        jobs,
        start=1
    ):
        lines.append(
            _retry_job_label(
                job,
                index
            )
        )

        lines.append(
            f"   وضعیت: {_retry_status_label(job.get('status'))}"
        )

        lines.append("")

    lines.append(
        "روی دکمه مورد موردنظر بزن یا دستور زیر را ارسال کن:"
    )

    lines.append(
        "/retry 1"
    )

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=_build_retry_keyboard(
            jobs
        )
    )


# =========================================================
# END COMMAND: /retry
# =========================================================


# =========================================================
# START CALLBACK: Retry buttons
# =========================================================

async def retry_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query is None:
        return

    try:
        await query.answer(
            "در حال بررسی..."
        )
    except Exception:
        print("")
        print("CALLBACK ANSWER FAILED:")
        traceback.print_exc()
        print("")

    if not is_allowed_user(update):

        await _safe_callback_message(
            query,
            "⛔ شما اجازه استفاده از این ربات را ندارید."
        )
        return

    callback_data = str(
        query.data or ""
    )

    prefix = "retryjob:"

    if not callback_data.startswith(
        prefix
    ):
        return

    job_id = callback_data[
        len(prefix):
    ].strip()

    if not job_id:
        await _safe_callback_message(
            query,
            "❌ شناسه پردازش نامعتبر است."
        )
        return

    await retry_job_by_id(
        update,
        job_id,
        context.application,
    )


# =========================================================
# END CALLBACK: Retry buttons
# =========================================================


# =========================================================
# START CALLBACK: Delete Retry buttons
# =========================================================

async def retry_delete_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query is None:
        return

    if not is_allowed_user(update):
        try:
            await query.answer(
                "دسترسی ندارید.",
                show_alert=True
            )
        except Exception:
            pass
        return

    callback_data = str(
        query.data or ""
    )

    prefix = "retrydelete:"

    if not callback_data.startswith(
        prefix
    ):
        return

    job_id = callback_data[
        len(prefix):
    ].strip()

    if not job_id:
        await query.answer(
            "شناسه پردازش نامعتبر است.",
            show_alert=True
        )
        return

    job_path = os.path.join(
        JOBS_DIR,
        f"{job_id}.json"
    )

    if not os.path.exists(job_path):
        await query.answer(
            "این پردازش پیدا نشد.",
            show_alert=True
        )
        return

    try:
        job = _load_job_file(
            job_path
        )
    except Exception:
        await query.answer(
            "فایل پردازش قابل خواندن نیست.",
            show_alert=True
        )
        return

    if int(job.get("telegramUserId") or 0) not in ALLOWED_USER_IDS:
        await query.answer(
            "مالک این پردازش در فهرست کاربران مجاز نیست.",
            show_alert=True
        )
        return

    if job.get("status") not in RETRYABLE_JOB_STATUSES:
        await query.answer(
            "این مورد دیگر در لیست Retry نیست.",
            show_alert=True
        )
        return

    retry_group = _get_retry_group_jobs(
        job
    )

    # Safety guard for old/stale Telegram messages:
    # a legacy "retrydeleteconfirm" button must never silently delete hidden
    # duplicates that were not explicitly shown to the operator.
    if len(retry_group) > 1:
        await query.answer(
            "این مورد چند Job تکراری دارد؛ از /retry لیست جدید را باز کن و حذف گروهی را تأیید کن.",
            show_alert=True
        )
        return


    target_ids = []

    for grouped_job in retry_group:
        grouped_job_id = str(
            grouped_job.get("jobId") or ""
        ).strip()

        if grouped_job_id:
            target_ids.append(
                grouped_job_id
            )

    target_ids = list(
        dict.fromkeys(
            target_ids
        )
    )

    try:
        await query.answer()
    except Exception:
        pass

    current_status = str(
        job.get("status") or ""
    ).strip()

    if len(target_ids) > 1:
        warning_lines = [
            "⚠️ این مورد با چند Job تکراری گروه شده است.",
            f"در صورت تأیید، هر {len(target_ids)} Job زیر حذف کامل می‌شوند:",
            "",
        ]

        for target_id in target_ids[:10]:
            warning_lines.append(
                f"• {target_id}"
            )

        if len(target_ids) > 10:
            warning_lines.append(
                f"• ... و {len(target_ids) - 10} Job دیگر"
            )

        warning_lines.extend(
            [
                "",
                "عکس‌ها + فایل‌های Job + رکوردهای Queue پاک می‌شوند.",
                "بعد از حذف، بازیابی از داخل ربات ممکن نیست.",
            ]
        )

        warning_text = "\n".join(
            warning_lines
        )

        confirm_callback = (
            f"retrydeletegroupconfirm:{job_id}"
        )

        confirm_text = (
            f"✅ حذف همه {len(target_ids)} مورد"
        )

    else:
        if current_status == "vision_failed":
            warning_text = (
                "⚠️ این Vision failed به‌طور کامل حذف شود؟\n"
                "عکس ذخیره‌شده + فایل Job + رکورد Queue پاک می‌شوند.\n"
                "بعد از حذف، Retry یا بازیابی از داخل ربات ممکن نیست.\n\n"
                f"Job ID: {job_id}"
            )
        else:
            warning_text = (
                "⚠️ این پردازش به‌طور کامل حذف شود؟\n"
                "عکس ذخیره‌شده + فایل Job + رکورد Queue پاک می‌شوند.\n"
                "ممکن است JSON استخراج‌شده هم از بین برود.\n\n"
                f"Job ID: {job_id}"
            )

        confirm_callback = (
            f"retrydeleteconfirm:{job_id}"
        )

        confirm_text = "✅ حذف کامل"

    await _safe_callback_message(
        query,
        warning_text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text=confirm_text,
                        callback_data=confirm_callback
                    ),
                    InlineKeyboardButton(
                        text="↩️ انصراف",
                        callback_data=f"retrydeletecancel:{job_id}"
                    )
                ]
            ]
        )
    )

async def retry_delete_confirm_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query is None:
        return

    if not is_allowed_user(update):
        try:
            await query.answer(
                "دسترسی ندارید.",
                show_alert=True
            )
        except Exception:
            pass
        return

    callback_data = str(
        query.data or ""
    )

    prefix = "retrydeleteconfirm:"

    if not callback_data.startswith(
        prefix
    ):
        return

    job_id = callback_data[
        len(prefix):
    ].strip()

    job_path = os.path.join(
        JOBS_DIR,
        f"{job_id}.json"
    )

    if not os.path.exists(job_path):
        await query.answer(
            "این پردازش پیدا نشد.",
            show_alert=True
        )
        return

    try:
        job = _load_job_file(
            job_path
        )
    except Exception:
        await query.answer(
            "فایل پردازش قابل خواندن نیست.",
            show_alert=True
        )
        return

    user_id = get_telegram_user_id(
        update
    )

    if int(job.get("telegramUserId") or 0) not in ALLOWED_USER_IDS:
        await query.answer(
            "مالک این پردازش در فهرست کاربران مجاز نیست.",
            show_alert=True
        )
        return

    if job.get("status") not in RETRYABLE_JOB_STATUSES:
        await query.answer(
            "این مورد دیگر در لیست Retry نیست.",
            show_alert=True
        )
        return

    retry_group = _get_retry_group_jobs(
        job
    )

    deleted_job_ids = []
    delete_errors = []
    touched_batch_ids = set()

    for grouped_job in retry_group:

        grouped_job_id = str(
            grouped_job.get("jobId") or ""
        ).strip()

        grouped_job_path = str(
            grouped_job.get("_jobPath")
            or os.path.join(
                JOBS_DIR,
                f"{grouped_job_id}.json"
            )
        ).strip()

        grouped_image_path = str(
            grouped_job.get("imagePath") or ""
        ).strip()

        grouped_batch_id = str(
            grouped_job.get("batchId") or ""
        ).strip()

        if grouped_batch_id:
            touched_batch_ids.add(
                grouped_batch_id
            )

        try:
            _db_delete_queue_job(
                grouped_job_id
            )
        except Exception as ex:
            delete_errors.append(
                f"DB {grouped_job_id}: {ex}"
            )
            continue

        for path in (
            grouped_image_path,
            grouped_job_path,
        ):
            if not path:
                continue

            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as ex:
                delete_errors.append(
                    f"{path}: {ex}"
                )

        deleted_job_ids.append(
            grouped_job_id
        )

    for grouped_batch_id in touched_batch_ids:
        try:
            _db_mark_batch_done_if_complete(
                grouped_batch_id
            )
        except Exception:
            traceback.print_exc()

    if delete_errors:
        message = (
            "⚠️ حذف انجام شد، اما بعضی فایل‌ها یا رکوردها کامل پاک نشدند.\n"
            f"تعداد Job حذف‌شده: {len(deleted_job_ids)}\n\n"
            + "\n".join(delete_errors[:5])
        )
    else:
        message = (
            "🗑 پردازش به‌طور کامل حذف شد.\n"
            "همه Jobهای Retry تکراری مربوط به همان عکس/لید نیز پاک شدند.\n"
            f"تعداد Job حذف‌شده: {len(deleted_job_ids)}"
        )

    try:
        await query.answer(
            "حذف کامل انجام شد."
        )
    except Exception:
        pass

    try:
        await query.edit_message_text(
            message
        )
    except Exception:
        await _safe_callback_message(
            query,
            message
        )


async def retry_delete_group_confirm_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query is None:
        return

    if not is_allowed_user(update):
        try:
            await query.answer(
                "دسترسی ندارید.",
                show_alert=True
            )
        except Exception:
            pass
        return

    callback_data = str(
        query.data or ""
    )

    prefix = "retrydeletegroupconfirm:"

    if not callback_data.startswith(
        prefix
    ):
        return

    job_id = callback_data[
        len(prefix):
    ].strip()

    job_path = os.path.join(
        JOBS_DIR,
        f"{job_id}.json"
    )

    if not os.path.exists(job_path):
        await query.answer(
            "این پردازش پیدا نشد.",
            show_alert=True
        )
        return

    try:
        job = _load_job_file(
            job_path
        )
    except Exception:
        await query.answer(
            "فایل پردازش قابل خواندن نیست.",
            show_alert=True
        )
        return

    if int(job.get("telegramUserId") or 0) not in ALLOWED_USER_IDS:
        await query.answer(
            "مالک این پردازش در فهرست کاربران مجاز نیست.",
            show_alert=True
        )
        return

    if job.get("status") not in RETRYABLE_JOB_STATUSES:
        await query.answer(
            "این مورد دیگر در لیست Retry نیست.",
            show_alert=True
        )
        return

    retry_group = _get_retry_group_jobs(
        job
    )

    if len(retry_group) <= 1:
        await query.answer(
            "این مورد دیگر گروه تکراری ندارد؛ از /retry لیست جدید را باز کن.",
            show_alert=True
        )
        return

    deleted_job_ids = []
    delete_errors = []
    touched_batch_ids = set()

    for grouped_job in retry_group:

        grouped_job_id = str(
            grouped_job.get("jobId") or ""
        ).strip()

        grouped_job_path = str(
            grouped_job.get("_jobPath")
            or os.path.join(
                JOBS_DIR,
                f"{grouped_job_id}.json"
            )
        ).strip()

        grouped_image_path = str(
            grouped_job.get("imagePath") or ""
        ).strip()

        grouped_batch_id = str(
            grouped_job.get("batchId") or ""
        ).strip()

        if grouped_batch_id:
            touched_batch_ids.add(
                grouped_batch_id
            )

        try:
            _db_delete_queue_job(
                grouped_job_id
            )
        except Exception as ex:
            delete_errors.append(
                f"DB {grouped_job_id}: {ex}"
            )
            continue

        for path in (
            grouped_image_path,
            grouped_job_path,
        ):
            if not path:
                continue

            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as ex:
                delete_errors.append(
                    f"{path}: {ex}"
                )

        deleted_job_ids.append(
            grouped_job_id
        )

    for grouped_batch_id in touched_batch_ids:
        try:
            _db_mark_batch_done_if_complete(
                grouped_batch_id
            )
        except Exception:
            traceback.print_exc()

    if delete_errors:
        message = (
            "⚠️ حذف گروهی انجام شد، اما بعضی فایل‌ها یا رکوردها کامل پاک نشدند.\n"
            f"تعداد Job حذف‌شده: {len(deleted_job_ids)}\n\n"
            + "\n".join(delete_errors[:5])
        )
    else:
        message = (
            "🗑 حذف گروهی کامل شد.\n"
            f"تعداد Job حذف‌شده: {len(deleted_job_ids)}\n\n"
            + "\n".join(
                f"• {deleted_id}"
                for deleted_id in deleted_job_ids[:10]
            )
        )

    try:
        await query.answer(
            "حذف گروهی انجام شد."
        )
    except Exception:
        pass

    try:
        await query.edit_message_text(
            message
        )
    except Exception:
        await _safe_callback_message(
            query,
            message
        )


async def retry_delete_cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query is None:
        return

    try:
        await query.answer(
            "لغو شد."
        )
    except Exception:
        pass

    try:
        await query.edit_message_text(
            "↩️ حذف Retry لغو شد."
        )
    except Exception:
        pass


# =========================================================
# END CALLBACK: Delete Retry buttons
# =========================================================


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = get_telegram_user_id(update)

    access_text = (
        "✅ دسترسی شما فعال است."
        if is_allowed_user(update)
        else "⛔ دسترسی شما هنوز فعال نیست."
    )

    await update.message.reply_text(
        "🤖 ربات آماده است.\n\n"
        f"Telegram User ID: {user_id}\n"
        f"{access_text}\n\n"
        "برای فعال شدن، این User ID باید داخل "
        "ALLOWED_USER_IDS قرار بگیرد."
    )


async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = get_telegram_user_id(update)

    await update.message.reply_text(
        f"Telegram User ID: {user_id}"
    )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_allowed_user(update):
        await deny_access(update)
        return

    await update.message.reply_text(
        "✅ Bot فعال است.\n"
        "✅ Vision Bridge فعال است.\n"
        "✅ ChatGPT Extension mode\n"
        "✅ Persistent Queue فعال است.\n"
        "✅ Vision به صورت سریالی پردازش می‌شود.\n"
        "✅ Google Sheet فروش دوره مذاکره configured"
    )


# =========================================================
# SHEET COMMAND
# =========================================================

def _sheet_destination_from_token(token):
    value = str(token or "").strip().lower()

    if value in (
        "course",
        "negotiation",
        "negotiation_course",
        "sale",
        "sales",
        "مذاکره",
        "فروش",
    ):
        return DESTINATION_COURSE

    if value in (
        "azar",
        "event",
        "azar_event",
        "conference",
        "همایش",
        "آذر",
    ):
        return DESTINATION_AZAR_EVENT

    return None


def _ensure_destination_config_container(destination):
    if destination == DESTINATION_COURSE and not isinstance(
        config.get("googleSheets"),
        dict,
    ):
        # Preserve the legacy production shape until config.json is migrated.
        config.setdefault("googleSheet", {})
        return config["googleSheet"]

    google_sheets = config.setdefault("googleSheets", {})
    destination_config = google_sheets.setdefault(destination, {})
    return destination_config


async def sheet_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_allowed_user(update):
        await deny_access(update)
        return

    args = list(context.args or [])

    if not args:
        course_url = get_google_sheet_url(DESTINATION_COURSE)
        azar_url = get_google_sheet_url(DESTINATION_AZAR_EVENT)

        await update.message.reply_text(
            "📄 تنظیمات Google Sheet:\n\n"
            "🎓 فروش دوره مذاکره:\n"
            f"{course_url or 'NOT CONFIGURED'}\n\n"
            "📅 همایش آذرماه:\n"
            f"{azar_url or 'NOT CONFIGURED'}\n\n"
            "دستورات:\n"
            "/sheet test course\n"
            "/sheet test azar\n"
            "/sheet set course URL\n"
            "/sheet set azar URL\n"
            "/sheet secret course SECRET\n"
            "/sheet secret azar SECRET\n\n"
            "دستورات قدیمی /sheet test و /sheet set URL و "
            "/sheet secret SECRET همچنان برای فروش دوره مذاکره کار می‌کنند."
        )
        return

    action = str(args[0]).strip().lower()

    if action == "test":
        destination = (
            _sheet_destination_from_token(args[1])
            if len(args) >= 2
            else DESTINATION_COURSE
        )

        if not destination:
            await update.message.reply_text(
                "❌ مقصد نامعتبر است. از course یا azar استفاده کن."
            )
            return

        if not is_destination_configured(destination):
            await update.message.reply_text(
                f"❌ Google Sheet «{DESTINATION_LABELS[destination]}» هنوز تنظیم نشده است."
            )
            return

        await update.message.reply_text(
            f"⏳ در حال تست Google Sheet «{DESTINATION_LABELS[destination]}»..."
        )

        try:
            result = await asyncio.to_thread(
                test_google_sheet_sync,
                destination,
            )

            if result.get("success") is True:
                await update.message.reply_text(
                    "✅ Google Sheet آنلاین است.\n\n"
                    f"مقصد: {DESTINATION_LABELS[destination]}\n"
                    f"Status: {result.get('status', '-')}\n"
                    f"Version: {result.get('version', '-')}"
                )
            else:
                await update.message.reply_text(
                    "⚠️ Google Sheet پاسخ داد ولی وضعیت موفق نبود.\n\n"
                    + json.dumps(result, ensure_ascii=False, indent=2)
                )
        except Exception as ex:
            print("")
            print("GOOGLE SHEET TEST ERROR:")
            traceback.print_exc()
            await update.message.reply_text(
                "❌ تست Google Sheet ناموفق بود.\n\n"
                + str(ex)
            )
        return

    if action == "set":
        # New syntax: /sheet set course URL
        # Legacy syntax: /sheet set URL  -> course
        if len(args) >= 3 and _sheet_destination_from_token(args[1]):
            destination = _sheet_destination_from_token(args[1])
            new_url = str(args[2]).strip()
        elif len(args) >= 2:
            destination = DESTINATION_COURSE
            new_url = str(args[1]).strip()
        else:
            await update.message.reply_text(
                "❌ آدرس وارد نشده.\n\n"
                "مثال:\n"
                "/sheet set course https://script.google.com/macros/s/.../exec"
            )
            return

        if not (
            new_url.startswith("https://")
            or new_url.startswith("http://")
        ):
            await update.message.reply_text("❌ آدرس معتبر نیست.")
            return

        if "script.google.com" not in new_url:
            await update.message.reply_text(
                "❌ این آدرس شبیه Google Apps Script Web App نیست."
            )
            return

        target = _ensure_destination_config_container(destination)
        old_url = target.get("url")
        target["url"] = new_url

        try:
            save_config()
        except Exception:
            if old_url is None:
                target.pop("url", None)
            else:
                target["url"] = old_url
            raise

        await update.message.reply_text(
            f"✅ آدرس Google Sheet «{DESTINATION_LABELS[destination]}» ذخیره شد."
        )
        return

    if action == "secret":
        # New syntax: /sheet secret course SECRET
        # Legacy syntax: /sheet secret SECRET -> course
        if len(args) >= 3 and _sheet_destination_from_token(args[1]):
            destination = _sheet_destination_from_token(args[1])
            new_secret = str(args[2]).strip()
        elif len(args) >= 2:
            destination = DESTINATION_COURSE
            new_secret = str(args[1]).strip()
        else:
            await update.message.reply_text(
                "❌ Secret وارد نشده.\n\n"
                "مثال:\n"
                "/sheet secret azar NEW_SECRET"
            )
            return

        if len(new_secret) < 4:
            await update.message.reply_text("❌ Secret خیلی کوتاه است.")
            return

        target = _ensure_destination_config_container(destination)
        old_secret = target.get("secret")
        target["secret"] = new_secret

        try:
            save_config()
        except Exception:
            if old_secret is None:
                target.pop("secret", None)
            else:
                target["secret"] = old_secret
            raise

        await update.message.reply_text(
            f"✅ Secret مربوط به «{DESTINATION_LABELS[destination]}» ذخیره شد.\n"
            "مقدار Secret در پیام نمایش داده نمی‌شود."
        )
        return

    await update.message.reply_text(
        "❌ دستور ناشناخته.\n\n"
        "/sheet\n"
        "/sheet test course\n"
        "/sheet test azar\n"
        "/sheet set course URL\n"
        "/sheet set azar URL\n"
        "/sheet secret course SECRET\n"
        "/sheet secret azar SECRET"
    )


# =========================================================
# DESTINATION CALLBACK + SERIAL VISION WORKER
# =========================================================

async def destination_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    if query is None:
        return

    if not is_allowed_user(update):
        try:
            await query.answer("دسترسی ندارید.", show_alert=True)
        except Exception:
            pass
        return

    raw = str(query.data or "")
    parts = raw.split(":", 2)

    if len(parts) != 3 or parts[0] != "dest":
        return

    destination = parts[1].strip()
    batch_id = parts[2].strip()

    if destination not in DESTINATION_LABELS:
        await query.answer("مقصد نامعتبر است.", show_alert=True)
        return

    batch = _db_get_batch(batch_id)
    if not batch:
        await query.answer("این Batch پیدا نشد.", show_alert=True)
        return

    user_id = get_telegram_user_id(update)
    if int(batch["telegram_user_id"]) != int(user_id or 0):
        await query.answer("این Batch متعلق به کاربر دیگری است.", show_alert=True)
        return

    current_state = str(batch.get("state") or "")
    current_destination = str(batch.get("destination") or "")

    if current_state not in ("collecting", "waiting_destination"):
        selected_label = DESTINATION_LABELS.get(
            current_destination,
            current_destination or "نامشخص",
        )
        await query.answer(
            f"مقصد این Batch قبلاً روی «{selected_label}» ثبت شده است.",
            show_alert=True,
        )
        return

    if not is_destination_configured(destination):
        await query.answer(
            "تنظیمات Google Sheet این مقصد هنوز کامل نشده است.",
            show_alert=True,
        )
        return

    assigned = _db_assign_batch_destination(
        batch_id,
        destination,
    )

    if not assigned:
        await query.answer("امکان ثبت مقصد وجود نداشت.", show_alert=True)
        return

    # Mirror destination into durable JSON sidecars.
    with _db_connect() as db:
        rows = db.execute(
            "SELECT job_json_path FROM queue_jobs WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()

    for row in rows:
        job_path = row["job_json_path"]
        if os.path.exists(job_path):
            try:
                update_persisted_job(
                    job_path,
                    destination=destination,
                    status="queued",
                    lastError=None,
                )
            except Exception:
                print("DESTINATION JSON UPDATE FAILED:", job_path)
                traceback.print_exc()

    count = _db_count_batch_jobs(batch_id)
    destination_label = DESTINATION_LABELS[destination]

    try:
        await query.answer("در صف پردازش قرار گرفت.")
    except Exception:
        pass

    try:
        await query.edit_message_text(
            f"✅ مقصد انتخاب شد: {destination_label}\n"
            f"📥 تعداد عکس: {count}\n"
            "⏳ عکس‌ها محفوظ هستند و یکی‌یکی پردازش می‌شوند."
        )
    except Exception:
        await _safe_callback_message(
            query,
            f"✅ مقصد انتخاب شد: {destination_label}\n"
            f"📥 تعداد عکس: {count}\n"
            "⏳ عکس‌ها محفوظ هستند و یکی‌یکی پردازش می‌شوند."
        )


async def _process_queued_job(application, queue_job):
    job_id = queue_job["job_id"]
    batch_id = queue_job["batch_id"]
    job_path = queue_job["job_json_path"]
    image_path = queue_job["image_path"]
    chat_id = int(queue_job["telegram_chat_id"])
    destination = queue_job.get("destination") or DESTINATION_COURSE
    attempts = int(queue_job.get("vision_attempts") or 1)

    if not os.path.exists(job_path):
        _db_update_queue_job(job_id, status="vision_failed")
        await _safe_bot_send(
            application.bot,
            chat_id,
            f"❌ فایل Job پیدا نشد.\nJob ID: {job_id}",
        )
        _db_mark_batch_done_if_complete(batch_id)
        return

    if not os.path.exists(image_path):
        update_persisted_job(
            job_path,
            status="vision_failed",
            lastError="Persisted image file is missing.",
            visionAttempts=attempts,
        )
        _db_update_queue_job(
            job_id,
            status="vision_failed",
        )
        await _safe_bot_send(
            application.bot,
            chat_id,
            f"❌ تصویر ذخیره‌شده پیدا نشد.\nJob ID: {job_id}",
        )
        _db_mark_batch_done_if_complete(batch_id)
        return

    update_persisted_job(
        job_path,
        status="processing_vision",
        destination=destination,
        visionAttempts=attempts,
        lastError=None,
    )

    await _safe_bot_send(
        application.bot,
        chat_id,
        f"🔍 در حال استخراج اطلاعات...\nJob ID: {job_id}",
    )

    try:
        async with processing_lock:
            data = await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_image_via_bridge,
                    image_path,
                ),
                timeout=VISION_JOB_TIMEOUT_SECONDS,
            )

        print("")
        print("VISION DATA:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print("")

        update_persisted_job(
            job_path,
            status="vision_done",
            destination=destination,
            data=data,
            lastError=None,
            visionAttempts=attempts,
        )
        _db_update_queue_job(
            job_id,
            status="vision_done",
        )

    except TimeoutError as ex:
        # A Vision job must never hold the serial queue for more than the
        # configured hard timeout. Do not auto-requeue timeouts, otherwise the
        # user could wait another full timeout window for the same image.
        timeout_error = (
            f"Vision processing exceeded {VISION_JOB_TIMEOUT_SECONDS} seconds."
        )
        update_persisted_job(
            job_path,
            status="vision_failed",
            destination=destination,
            lastError=timeout_error,
            visionAttempts=attempts,
        )
        _db_update_queue_job(
            job_id,
            status="vision_failed",
        )
        await _safe_bot_send(
            application.bot,
            chat_id,
            f"⏱️ پردازش تصویر پس از {VISION_JOB_TIMEOUT_SECONDS} ثانیه متوقف شد. "
            "عکس محفوظ مانده است و دوباره خودکار پردازش نمی‌شود.\n"
            f"Job ID: {job_id}",
        )
        print(
            "VISION HARD TIMEOUT:",
            job_id,
            str(ex),
        )
        _db_mark_batch_done_if_complete(batch_id)
        return

    except json.JSONDecodeError as ex:
        # Invalid JSON may still be transient, so keep the existing one retry.
        if attempts < 2:
            update_persisted_job(
                job_path,
                status="queued",
                destination=destination,
                lastError=str(ex),
                visionAttempts=attempts,
            )
            _db_update_queue_job(
                job_id,
                status="queued",
            )
            await _safe_bot_send(
                application.bot,
                chat_id,
                "⚠️ پاسخ Vision معتبر نبود؛ عکس محفوظ است و یک بار دیگر تلاش می‌شود.\n"
                f"Job ID: {job_id}",
            )
        else:
            update_persisted_job(
                job_path,
                status="vision_failed",
                destination=destination,
                lastError=str(ex),
                visionAttempts=attempts,
            )
            _db_update_queue_job(
                job_id,
                status="vision_failed",
            )
            await _safe_bot_send(
                application.bot,
                chat_id,
                "⚠️ Vision بعد از دو تلاش پاسخ معتبر نداد. عکس محفوظ مانده است.\n"
                f"Job ID: {job_id}",
            )
        _db_mark_batch_done_if_complete(batch_id)
        return

    except Exception as ex:
        update_persisted_job(
            job_path,
            status="vision_failed",
            destination=destination,
            lastError=str(ex),
            visionAttempts=attempts,
        )
        _db_update_queue_job(
            job_id,
            status="vision_failed",
        )
        print("")
        print("VISION WORKER ERROR:")
        traceback.print_exc()
        print("")
        await _safe_bot_send(
            application.bot,
            chat_id,
            f"❌ پردازش Vision متوقف شد، اما عکس محفوظ است.\nJob ID: {job_id}",
        )
        _db_mark_batch_done_if_complete(batch_id)
        return

    # From this point onward Vision MUST NOT run again for Sheet failures.
    request_id = job_id

    try:
        update_persisted_job(
            job_path,
            status="sheet_pending",
            destination=destination,
            requestId=request_id,
            lastError=None,
        )
        _db_update_queue_job(
            job_id,
            status="sheet_pending",
        )

        destination_label = DESTINATION_LABELS.get(
            destination,
            destination,
        )

        await _safe_bot_send(
            application.bot,
            chat_id,
            f"⏳ اطلاعات استخراج شد. در حال ارسال به «{destination_label}»...\n"
            f"Job ID: {job_id}",
        )

        google_response = await asyncio.to_thread(
            send_to_google_sheet_sync,
            data,
            image_path,
            request_id,
            False,
            destination,
        )

    except Exception as sheet_ex:
        update_persisted_job(
            job_path,
            status="sheet_unconfirmed",
            destination=destination,
            requestId=request_id,
            lastError=str(sheet_ex),
        )
        _db_update_queue_job(
            job_id,
            status="sheet_unconfirmed",
        )

        print("")
        print("GOOGLE SHEET FAILED AFTER VISION SUCCESS:")
        traceback.print_exc()
        print("")

        await _safe_bot_send(
            application.bot,
            chat_id,
            "⚠️ اطلاعات ارسال شد، اما تأیید نهایی از Google دریافت نشد.\n\n"
            "ممکن است اطلاعات در شیت ثبت شده باشد.\n"
            "برای جلوگیری از ثبت تکراری، ارسال مجدد خودکار انجام نمی‌شود.\n\n"
            f"Job ID: {job_id}\n"
            "برای بررسی وضعیت، /retry را بزن.",
        )
        _db_mark_batch_done_if_complete(batch_id)
        return

    print("GOOGLE SHEET RESPONSE:")
    print(json.dumps(google_response, ensure_ascii=False, indent=2))
    print("")

    if google_response.get("requestStatus") == "submitted_unconfirmed":
        update_persisted_job(
            job_path,
            status="sheet_submitted",
            destination=destination,
            requestId=request_id,
            googleResponse=google_response,
            lastError=None,
        )
        _db_update_queue_job(
            job_id,
            status="sheet_submitted",
        )

        await _safe_bot_send(
            application.bot,
            chat_id,
            "✅ اطلاعات به Google ارسال شد.\n"
            "ℹ️ تأیید نهایی هنوز دریافت نشده است؛ ممکن است رکورد همین حالا در شیت ثبت شده باشد.\n"
            "برای جلوگیری از ثبت تکراری، ارسال مجدد خودکار انجام نمی‌شود.\n\n"
            f"Job ID: {job_id}\n"
            "برای بررسی وضعیت، /retry را بزن.",
        )

    elif google_response.get("duplicate") is True:
        update_persisted_job(
            job_path,
            status="completed_duplicate",
            destination=destination,
            requestId=request_id,
            googleResponse=google_response,
            lastError=None,
        )
        _db_update_queue_job(
            job_id,
            status="completed_duplicate",
        )

        await _safe_bot_send(
            application.bot,
            chat_id,
            _duplicate_message_from_response(
                data,
                google_response,
            ),
        )

    elif google_response.get("success") is True:
        update_persisted_job(
            job_path,
            status="completed",
            destination=destination,
            requestId=request_id,
            googleResponse=google_response,
            lastError=None,
        )
        _db_update_queue_job(
            job_id,
            status="completed",
        )

        destination_label = DESTINATION_LABELS.get(
            destination,
            destination,
        )

        result_text = (
            f"✅ اطلاعات با موفقیت در «{destination_label}» ثبت شد.\n\n"
            f"Instagram ID: {data.get('instagramId') or '-'}\n"
            f"نام: {data.get('fullName') or '-'}\n"
            f"شماره تماس: {data.get('phoneNumber') or '-'}\n"
            f"شغل: {data.get('job') or '-'}\n"
            f"تعداد پرسنل: {data.get('employeeCount') or '-'}\n"
            f"شهر: {data.get('city') or '-'}\n"
            f"چالش: {data.get('challenge') or '-'}\n"
            f"توضیحات: {data.get('description') or '-'}"
        )

        await _safe_bot_send(
            application.bot,
            chat_id,
            result_text,
        )

    else:
        error_message = (
            google_response.get("message")
            or google_response.get("error")
            or "خطای نامشخص"
        )

        update_persisted_job(
            job_path,
            status="sheet_failed",
            destination=destination,
            requestId=request_id,
            googleResponse=google_response,
            lastError=str(error_message),
        )
        _db_update_queue_job(
            job_id,
            status="sheet_failed",
        )

        await _safe_bot_send(
            application.bot,
            chat_id,
            "⚠️ JSON استخراج‌شده محفوظ است، اما Google Sheet درخواست را نپذیرفت.\n\n"
            f"Job ID: {job_id}\n"
            f"علت: {error_message}\n\n"
            "🚫 Vision دوباره اجرا نمی‌شود. برای Retry امن /retry را بزن.",
        )

    _db_mark_batch_done_if_complete(batch_id)


async def vision_queue_worker(application):
    print("Persistent Vision Queue Worker started.")

    while True:
        try:
            queue_job = await asyncio.to_thread(
                _db_claim_next_job
            )

            if not queue_job:
                await asyncio.sleep(1.0)
                continue

            await _process_queued_job(
                application,
                queue_job,
            )

        except asyncio.CancelledError:
            raise
        except Exception:
            print("")
            print("VISION QUEUE WORKER ERROR:")
            traceback.print_exc()
            print("")
            await asyncio.sleep(2.0)


async def _ensure_vision_queue_worker(application):
    """
    Ensure the serial Vision worker is alive.

    Manual Retry must never only change persisted state and then silently wait
    forever because the background worker task is missing/cancelled.
    """
    global queue_worker_task

    if (
        queue_worker_task is not None
        and not queue_worker_task.done()
    ):
        return False

    if queue_worker_task is not None:
        try:
            worker_error = queue_worker_task.exception()
            if worker_error:
                print(
                    "VISION QUEUE WORKER WAS STOPPED:",
                    repr(worker_error),
                )
        except asyncio.CancelledError:
            print(
                "VISION QUEUE WORKER WAS CANCELLED; restarting."
            )
        except Exception as ex:
            print(
                "VISION QUEUE WORKER STATE CHECK FAILED:",
                repr(ex),
            )

    queue_worker_task = application.create_task(
        vision_queue_worker(application)
    )

    print(
        "VISION QUEUE WORKER STARTED/RECOVERED."
    )

    return True


async def post_init(application):
    await asyncio.to_thread(
        init_queue_db
    )

    await _recover_incomplete_downloads(application)
    await _recover_destination_prompts(application)

    await _ensure_vision_queue_worker(
        application
    )

    print("")
    print("Queue DB:", QUEUE_DB_PATH)
    print("Batch collection window:", BATCH_COLLECT_SECONDS, "seconds")
    print("Serial Vision worker: ENABLED")
    print("")


# =========================================================
# END DESTINATION CALLBACK + SERIAL VISION WORKER
# =========================================================

# =========================================================
# PHOTO HANDLER
# =========================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # Access check happens BEFORE image download and BEFORE Bridge/ChatGPT.
    if not is_allowed_user(update):
        await deny_access(update)
        return

    if not update.message or not update.message.photo:
        return

    user_id = get_telegram_user_id(update)
    chat_id = update.effective_chat.id if update.effective_chat else None

    if not user_id or chat_id is None:
        return

    await safe_reply(
        update,
        "✅ تصویر دریافت شد. در حال ذخیره امن فایل..."
    )

    job_id = None
    job_path = None
    image_path = None

    try:
        # Batch assignment is deliberately tiny and protected separately.
        # No Vision/Google work happens under this lock.
        async with batch_state_lock:
            batch = await asyncio.to_thread(
                _db_get_collecting_batch,
                user_id,
                chat_id,
            )

            if batch:
                batch_id = batch["batch_id"]
                await asyncio.to_thread(
                    _db_touch_batch,
                    batch_id,
                )
            else:
                batch_id = await asyncio.to_thread(
                    _db_create_batch,
                    user_id,
                    chat_id,
                )

        photo = update.message.photo[-1]
        message_id = update.message.message_id
        job_id = f"{user_id}_{message_id}_{uuid.uuid4().hex[:8]}"
        image_path = os.path.join(
            JOBS_DIR,
            job_id + ".jpg",
        )
        job_path = os.path.join(
            JOBS_DIR,
            job_id + ".json",
        )

        # Persist metadata BEFORE the network download. If the process dies
        # during download, post_init can use telegramFileId to recover it.
        job = {
            "jobId": job_id,
            "batchId": batch_id,
            "status": "receiving",
            "destination": None,
            "createdAt": _utc_now_iso(),
            "updatedAt": _utc_now_iso(),
            "telegramUserId": user_id,
            "telegramChatId": chat_id,
            "telegramMessageId": message_id,
            "telegramFileId": photo.file_id,
            "imagePath": image_path,
            "data": None,
            "requestId": None,
            "googleResponse": None,
            "lastError": None,
            "visionAttempts": 0,
        }

        _write_json_atomic(
            job_path,
            job,
        )

        await asyncio.to_thread(
            _db_insert_queue_job,
            {
                **job,
                "jobPath": job_path,
            },
        )

        telegram_file = await context.bot.get_file(
            photo.file_id
        )

        await telegram_file.download_to_drive(
            custom_path=image_path
        )

        update_persisted_job(
            job_path,
            status="waiting_destination",
            lastError=None,
        )

        print("")
        print("----------------------------------------")
        print("PHOTO SAFELY PERSISTED")
        print("Batch ID:", batch_id)
        print("Job ID:", job_id)
        print("Image:", image_path)
        print("Job:", job_path)
        print("")

        await safe_reply(
            update,
            "✅ عکس با موفقیت ذخیره شد."
        )

        _schedule_batch_finalize(
            context.application,
            user_id,
            batch_id,
        )

    except Exception as ex:
        print("")
        print("PHOTO INTAKE ERROR:")
        traceback.print_exc()
        print("")

        if job_path and os.path.exists(job_path):
            try:
                update_persisted_job(
                    job_path,
                    status="receive_failed",
                    lastError=str(ex),
                )
            except Exception:
                traceback.print_exc()

        await safe_reply(
            update,
            "⚠️ دریافت فایل کامل نشد، اما اطلاعات بازیابی عکس ذخیره شده است.\n"
            "در اجرای بعدی ربات، بازیابی خودکار تلاش می‌شود.\n\n"
            f"Job ID: {job_id or '-'}"
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    error = context.error

    # Telegram long-polling/network interruptions are expected on an
    # unstable route/VPN. python-telegram-bot reconnects automatically.
    # Do not flood the console with hundreds of traceback lines.
    if isinstance(error, (NetworkError, TimedOut)):

        print("")
        print(
            "TELEGRAM NETWORK WARNING:",
            type(error).__name__,
            str(error)
        )
        print("Polling will continue automatically.")
        print("")
        return

    print("")
    print("TELEGRAM ERROR:")

    traceback.print_exception(
        type(error),
        error,
        error.__traceback__
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print("")
    print("========================================")
    print("   ChatGPT Telegram Vision Bot")
    print("========================================")
    print("")

    print("BOT STARTED")

    print(
        "Google Sheet - فروش دوره مذاکره:",
        get_google_sheet_url(DESTINATION_COURSE) or "NOT CONFIGURED"
    )
    print(
        "Google Sheet - همایش آذرماه:",
        get_google_sheet_url(DESTINATION_AZAR_EVENT) or "NOT CONFIGURED"
    )

    print("")
    print("Mode: Extension + Local Bridge")
    print("")
    print(
        "Google POST timeout:",
        f"connect={GOOGLE_POST_CONNECT_TIMEOUT_SECONDS}s,",
        f"read={GOOGLE_POST_READ_TIMEOUT_SECONDS}s"
    )
    print(
        "Google STATUS:",
        f"connect={GOOGLE_STATUS_CONNECT_TIMEOUT_SECONDS}s,",
        f"read={GOOGLE_STATUS_READ_TIMEOUT_SECONDS}s,",
        f"attempts={GOOGLE_STATUS_POLL_ATTEMPTS},",
        f"initial_delay={GOOGLE_STATUS_INITIAL_DELAY_SECONDS}s,",
        f"delay={GOOGLE_STATUS_POLL_DELAY_SECONDS}s"
    )
    print(
        "Google AUTO POST RETRY:",
        GOOGLE_AUTO_POST_RETRY_COUNT
    )
    print("")
    print(
        "Telegram API timeout:",
        f"connect={TELEGRAM_CONNECT_TIMEOUT_SECONDS}s,",
        f"read={TELEGRAM_READ_TIMEOUT_SECONDS}s,",
        f"write={TELEGRAM_WRITE_TIMEOUT_SECONDS}s,",
        f"pool={TELEGRAM_POOL_TIMEOUT_SECONDS}s"
    )
    print(
        "Telegram polling:",
        f"connect={TELEGRAM_POLL_CONNECT_TIMEOUT_SECONDS}s,",
        f"read={TELEGRAM_POLL_READ_TIMEOUT_SECONDS}s,",
        f"long_poll={TELEGRAM_LONG_POLL_TIMEOUT_SECONDS}s,",
        f"drop_pending={TELEGRAM_DROP_PENDING_UPDATES}"
    )
    print("")
    print("Waiting for Telegram...")
    print("")


    # =====================================================
    # TELEGRAM HTTP CLIENTS
    #
    # Two separate clients:
    # 1) normal Bot API requests
    # 2) long-poll getUpdates
    #
    # This prevents a stuck long-poll connection from consuming the
    # same small connection pool used by sendMessage/getFile/download.
    # =====================================================

    telegram_request = HTTPXRequest(
        connection_pool_size=TELEGRAM_CONNECTION_POOL_SIZE,
        connect_timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
        read_timeout=TELEGRAM_READ_TIMEOUT_SECONDS,
        write_timeout=TELEGRAM_WRITE_TIMEOUT_SECONDS,
        pool_timeout=TELEGRAM_POOL_TIMEOUT_SECONDS,
        http_version="1.1",
    )

    telegram_get_updates_request = HTTPXRequest(
        connection_pool_size=TELEGRAM_POLL_CONNECTION_POOL_SIZE,
        connect_timeout=TELEGRAM_POLL_CONNECT_TIMEOUT_SECONDS,
        read_timeout=TELEGRAM_POLL_READ_TIMEOUT_SECONDS,
        write_timeout=TELEGRAM_POLL_WRITE_TIMEOUT_SECONDS,
        pool_timeout=TELEGRAM_POLL_POOL_TIMEOUT_SECONDS,
        http_version="1.1",
    )

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(telegram_request)
        .get_updates_request(telegram_get_updates_request)
        .post_init(post_init)
        .build()
    )


    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )


    app.add_handler(
        CommandHandler(
            "myid",
            myid
        )
    )


    app.add_handler(
        CommandHandler(
            "status",
            status
        )
    )


    app.add_handler(
        CommandHandler(
            "retry",
            retry_command
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            destination_callback,
            pattern=r"^dest:"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            retry_callback,
            pattern=r"^retryjob:"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            retry_delete_callback,
            pattern=r"^retrydelete:"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            retry_delete_confirm_callback,
            pattern=r"^retrydeleteconfirm:"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            retry_delete_group_confirm_callback,
            pattern=r"^retrydeletegroupconfirm:"
        )
    )


    app.add_handler(
        CallbackQueryHandler(
            retry_delete_cancel_callback,
            pattern=r"^retrydeletecancel:"
        )
    )


    app.add_handler(
        CommandHandler(
            "sheet",
            sheet_command
        )
    )


    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )


    app.add_error_handler(
        error_handler
    )


    app.run_polling(
        poll_interval=TELEGRAM_POLL_INTERVAL_SECONDS,
        timeout=TELEGRAM_LONG_POLL_TIMEOUT_SECONDS,
        bootstrap_retries=-1,
        drop_pending_updates=TELEGRAM_DROP_PENDING_UPDATES
    )


if __name__ == "__main__":
    main()
