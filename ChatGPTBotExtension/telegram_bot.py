import os
import sys
import json
import asyncio
import tempfile
import traceback
import ctypes
import requests

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from vision_bridge_client import analyze_image_via_bridge


# =========================================================
# SINGLE INSTANCE
# =========================================================

BOT_MUTEX_NAME = "Local\\ChatGPTTelegramBot_SingleInstance"

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


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


config = load_config()

BOT_TOKEN = config["telegramBotToken"]

GOOGLE_SHEET_URL = config["googleSheet"]["url"]
GOOGLE_SHEET_SECRET = config["googleSheet"]["secret"]


# =========================================================
# PROCESSING LOCK
# =========================================================

processing_lock = asyncio.Lock()


# =========================================================
# GOOGLE SHEET
# =========================================================

def send_to_google_sheet_sync(data):
    payload = {
        "secret": GOOGLE_SHEET_SECRET,
        "instagramId": data["instagramId"],
        "fullName": data["fullName"],
        "phoneNumber": data["phoneNumber"],
        "job": data["job"],
        "employeeCount": data["employeeCount"],
        "city": data["city"],
        "challenge": data["challenge"],
        "description": data["description"],
    }

    response = requests.post(
        GOOGLE_SHEET_URL,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    return response.text


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "✅ ربات آماده است.\n\n"
        "عکس را ارسال کن."
    )


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "✅ Bot فعال است.\n"
        "✅ Vision Bridge فعال است.\n"
        "✅ ChatGPT Extension mode"
    )


# =========================================================
# PHOTO HANDLER
# =========================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    temp_path = None

    async with processing_lock:

        try:
            await update.message.reply_text(
                "⏳ در حال بررسی تصویر..."
            )

            # -----------------------------------------
            # DOWNLOAD TELEGRAM PHOTO
            # -----------------------------------------

            photo = update.message.photo[-1]

            telegram_file = await context.bot.get_file(
                photo.file_id
            )

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".jpg"
            ) as tmp:
                temp_path = tmp.name

            await telegram_file.download_to_drive(
                custom_path=temp_path
            )

            print("")
            print("----------------------------------------")
            print("PHOTO RECEIVED")
            print("Temp file:", temp_path)
            print("")

            # -----------------------------------------
            # CHATGPT VISION THROUGH BRIDGE
            # -----------------------------------------

            data = await asyncio.to_thread(
                analyze_image_via_bridge,
                temp_path
            )

            print("")
            print("VISION DATA:")
            print(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2
                )
            )
            print("")

            # -----------------------------------------
            # GOOGLE SHEET
            # -----------------------------------------

            google_response = await asyncio.to_thread(
                send_to_google_sheet_sync,
                data
            )

            print("GOOGLE SHEET RESPONSE:")
            print(google_response)
            print("")

            # -----------------------------------------
            # TELEGRAM RESULT
            # -----------------------------------------

            result_text = (
                "✅ اطلاعات با موفقیت ثبت شد.\n\n"
                f"Instagram ID: {data['instagramId'] or '-'}\n"
                f"نام: {data['fullName'] or '-'}\n"
                f"شماره تماس: {data['phoneNumber'] or '-'}\n"
                f"شغل: {data['job'] or '-'}\n"
                f"تعداد پرسنل: {data['employeeCount'] or '-'}\n"
                f"شهر: {data['city'] or '-'}\n"
                f"چالش: {data['challenge'] or '-'}\n"
                f"توضیحات: {data['description'] or '-'}"
            )

            await update.message.reply_text(
                result_text
            )

        # ---------------------------------------------
        # JSON ERROR
        # ---------------------------------------------

        except json.JSONDecodeError:

            print("")
            print("JSON PARSE ERROR:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ پاسخ ChatGPT JSON معتبر نبود "
                "و چیزی ثبت نشد."
            )

        # ---------------------------------------------
        # NETWORK ERROR
        # ---------------------------------------------

        except requests.RequestException:

            print("")
            print("NETWORK ERROR:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ ارتباط با Bridge یا Google Sheet خطا داشت."
            )

        # ---------------------------------------------
        # TIMEOUT
        # ---------------------------------------------

        except TimeoutError:

            print("")
            print("VISION TIMEOUT:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ زمان انتظار برای پاسخ ChatGPT تمام شد."
            )

        # ---------------------------------------------
        # GENERAL ERROR
        # ---------------------------------------------

        except Exception as ex:

            print("")
            print("GENERAL ERROR:")
            traceback.print_exc()

            await update.message.reply_text(
                f"❌ خطا در پردازش تصویر:\n{str(ex)}"
            )

        finally:

            if (
                temp_path
                and
                os.path.exists(temp_path)
            ):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    print("")
    print("TELEGRAM ERROR:")

    traceback.print_exception(
        type(context.error),
        context.error,
        context.error.__traceback__
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
    print("Google Sheet:", GOOGLE_SHEET_URL)
    print("")
    print("Mode: Extension + Local Bridge")
    print("")
    print("Waiting for Telegram...")
    print("")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
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
            "status",
            status
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
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()