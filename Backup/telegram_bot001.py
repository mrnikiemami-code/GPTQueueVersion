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

BOT_TOKEN = config["telegramBotToken"]


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




def get_google_sheet_url():
    return config["googleSheet"]["url"]


def get_google_sheet_secret():
    return config["googleSheet"]["secret"]


# =========================================================
# PROCESSING LOCK
# =========================================================

processing_lock = asyncio.Lock()


# =========================================================
# GOOGLE SHEET
# =========================================================

def send_to_google_sheet_sync(data, image_path=None):

    payload = {
        "secret": get_google_sheet_secret(),
        "instagramId": data["instagramId"],
        "fullName": data["fullName"],
        "phoneNumber": data["phoneNumber"],
        "job": data["job"],
        "employeeCount": data["employeeCount"],
        "city": data["city"],
        "challenge": data["challenge"],
        "description": data["description"],
    }

    # =====================================================
    # IMAGE -> GOOGLE APPS SCRIPT / DRIVE
    # =====================================================

    if image_path and os.path.exists(image_path):

        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()

        payload["imageBase64"] = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        payload["imageFileName"] = (
            os.path.basename(image_path)
        )

        mime_type, _ = mimetypes.guess_type(
            image_path
        )

        payload["imageMimeType"] = (
            mime_type or "image/jpeg"
        )

    response = requests.post(
        get_google_sheet_url(),
        json=payload,
        timeout=60,
    )

    response.raise_for_status()

    try:
        result = response.json()

    except Exception:
        raise RuntimeError(
            "Google Sheet response is not valid JSON: "
            + response.text
        )

    # =====================================================
    # فقط پاسخ واقعی doPost قابل قبول است
    # =====================================================

    if (
        "duplicate" not in result
        and
        "row" not in result
    ):
        raise RuntimeError(
            "Google Sheet response was not a POST result: "
            + json.dumps(
                result,
                ensure_ascii=False
            )
        )

    return result


def test_google_sheet_sync():

    response = requests.get(
        get_google_sheet_url(),
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
        "✅ Google Sheet configured"
    )


# =========================================================
# SHEET COMMAND
# =========================================================

async def sheet_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_allowed_user(update):
        await deny_access(update)
        return

    args = context.args


    # -----------------------------------------------------
    # /sheet
    # -----------------------------------------------------

    if not args:

        await update.message.reply_text(
            "📄 تنظیمات فعلی Google Sheet:\n\n"
            f"URL:\n{get_google_sheet_url()}\n\n"
            "Secret: ********\n\n"
            "دستورات:\n"
            "/sheet test\n"
            "/sheet set URL\n"
            "/sheet secret SECRET"
        )

        return


    action = args[0].lower()


    # -----------------------------------------------------
    # /sheet test
    # -----------------------------------------------------

    if action == "test":

        await update.message.reply_text(
            "⏳ در حال تست Google Sheet..."
        )

        try:

            result = await asyncio.to_thread(
                test_google_sheet_sync
            )

            print("")
            print("GOOGLE SHEET TEST:")
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2
                )
            )
            print("")

            if result.get("success") is True:

                version = result.get(
                    "version",
                    "-"
                )

                status_value = result.get(
                    "status",
                    "-"
                )

                await update.message.reply_text(
                    "✅ Google Sheet آنلاین است.\n\n"
                    f"Status: {status_value}\n"
                    f"Version: {version}"
                )

            else:

                await update.message.reply_text(
                    "⚠️ Google Sheet پاسخ داد "
                    "ولی وضعیت موفق نبود.\n\n"
                    + json.dumps(
                        result,
                        ensure_ascii=False,
                        indent=2
                    )
                )

        except Exception as ex:

            print("")
            print("GOOGLE SHEET TEST ERROR:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ تست Google Sheet ناموفق بود.\n\n"
                f"{str(ex)}"
            )

        return


    # -----------------------------------------------------
    # /sheet set URL
    # -----------------------------------------------------

    if action == "set":

        if len(args) < 2:

            await update.message.reply_text(
                "❌ آدرس وارد نشده.\n\n"
                "مثال:\n"
                "/sheet set https://script.google.com/macros/s/.../exec"
            )

            return


        new_url = args[1].strip()


        if not (
            new_url.startswith("https://")
            or new_url.startswith("http://")
        ):

            await update.message.reply_text(
                "❌ آدرس معتبر نیست."
            )

            return


        if "script.google.com" not in new_url:

            await update.message.reply_text(
                "❌ این آدرس شبیه Google Apps Script Web App نیست."
            )

            return


        old_url = get_google_sheet_url()

        config["googleSheet"]["url"] = new_url

        try:

            save_config()

        except Exception:

            config["googleSheet"]["url"] = old_url

            raise


        await update.message.reply_text(
            "✅ آدرس Google Sheet تغییر کرد.\n\n"
            f"{new_url}\n\n"
            "این تنظیم بعد از Restart هم باقی می‌ماند."
        )

        print("")
        print("GOOGLE SHEET URL CHANGED:")
        print(new_url)
        print("")

        return


    # -----------------------------------------------------
    # /sheet secret SECRET
    # -----------------------------------------------------

    if action == "secret":

        if len(args) < 2:

            await update.message.reply_text(
                "❌ Secret وارد نشده.\n\n"
                "مثال:\n"
                "/sheet secret NEW_SECRET"
            )

            return


        new_secret = args[1].strip()


        if len(new_secret) < 4:

            await update.message.reply_text(
                "❌ Secret خیلی کوتاه است."
            )

            return


        old_secret = get_google_sheet_secret()

        config["googleSheet"]["secret"] = new_secret

        try:

            save_config()

        except Exception:

            config["googleSheet"]["secret"] = old_secret

            raise


        await update.message.reply_text(
            "✅ Secret جدید ذخیره شد.\n\n"
            "این مقدار در پیام نمایش داده نمی‌شود "
            "و بعد از Restart هم باقی می‌ماند."
        )

        print("")
        print("GOOGLE SHEET SECRET CHANGED")
        print("")

        return


    # -----------------------------------------------------
    # UNKNOWN
    # -----------------------------------------------------

    await update.message.reply_text(
        "❌ دستور ناشناخته.\n\n"
        "دستورات معتبر:\n"
        "/sheet\n"
        "/sheet test\n"
        "/sheet set URL\n"
        "/sheet secret SECRET"
    )


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

    temp_path = None

    async with processing_lock:

        try:

            await update.message.reply_text(
                "⏳ در حال بررسی تصویر..."
            )


            # =================================================
            # 1) DOWNLOAD TELEGRAM PHOTO
            # =================================================

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


            # =================================================
            # 2) CHATGPT VISION THROUGH BRIDGE
            # =================================================

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


            # =================================================
            # 3) GOOGLE SHEET
            # =================================================

            google_response = await asyncio.to_thread(
                send_to_google_sheet_sync,
                data,
                temp_path
            )


            print("GOOGLE SHEET RESPONSE:")

            print(
                json.dumps(
                    google_response,
                    ensure_ascii=False,
                    indent=2
                )
            )

            print("")


            # =================================================
            # 4) DUPLICATE
            # =================================================

            if google_response.get("duplicate") is True:

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
                        duplicate_value = data[
                            "instagramId"
                        ]


                elif duplicate_by == "phoneNumber":

                    duplicate_field = "شماره تماس"

                    if not duplicate_value:
                        duplicate_value = data[
                            "phoneNumber"
                        ]


                else:

                    duplicate_field = "اطلاعات مشابه"


                duplicate_message = (
                    "⛔ رکورد تکراری است و دوباره ثبت نشد.\n\n"
                    f"مورد تکراری: {duplicate_field}\n"
                    f"مقدار: {duplicate_value or '-'}\n"
                    f"ردیف قبلی: {duplicate_row or '-'}"
                )


                await update.message.reply_text(
                    duplicate_message
                )


                print("")
                print("DUPLICATE RECORD:")
                print(duplicate_message)
                print("")

                return


            # =================================================
            # 5) SHEET REJECTED
            # =================================================

            if google_response.get("success") is not True:

                error_message = (
                    google_response.get("message")
                    or
                    google_response.get("error")
                    or
                    "خطای نامشخص"
                )

                raise RuntimeError(
                    "Google Sheet rejected request: "
                    + str(error_message)
                )


            # =================================================
            # 6) SUCCESS
            # =================================================

            result_text = (

                "✅ اطلاعات با موفقیت ثبت شد.\n\n"

                f"Instagram ID: "
                f"{data['instagramId'] or '-'}\n"

                f"نام: "
                f"{data['fullName'] or '-'}\n"

                f"شماره تماس: "
                f"{data['phoneNumber'] or '-'}\n"

                f"شغل: "
                f"{data['job'] or '-'}\n"

                f"تعداد پرسنل: "
                f"{data['employeeCount'] or '-'}\n"

                f"شهر: "
                f"{data['city'] or '-'}\n"

                f"چالش: "
                f"{data['challenge'] or '-'}\n"

                f"توضیحات: "
                f"{data['description'] or '-'}"
            )


            await update.message.reply_text(
                result_text
            )


        # =====================================================
        # JSON ERROR
        # =====================================================

        except json.JSONDecodeError:

            print("")
            print("JSON PARSE ERROR:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ پاسخ ChatGPT JSON معتبر نبود "
                "و چیزی ثبت نشد."
            )


        # =====================================================
        # NETWORK ERROR
        # =====================================================

        except requests.RequestException:

            print("")
            print("NETWORK ERROR:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ ارتباط با Bridge یا Google Sheet خطا داشت."
            )


        # =====================================================
        # TIMEOUT
        # =====================================================

        except TimeoutError:

            print("")
            print("VISION TIMEOUT:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ زمان انتظار برای پاسخ ChatGPT تمام شد."
            )


        # =====================================================
        # GENERAL ERROR
        # =====================================================

        except Exception as ex:

            print("")
            print("GENERAL ERROR:")
            traceback.print_exc()

            await update.message.reply_text(
                "❌ خطا در پردازش تصویر:\n"
                + str(ex)
            )


        # =====================================================
        # CLEANUP
        # =====================================================

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

    print(
        "Google Sheet:",
        get_google_sheet_url()
    )

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
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()