import os
import time
import json
import uuid
import base64
import mimetypes
import requests


BRIDGE_URL = "http://127.0.0.1:8766"


VISION_PROMPT = (
    'این تصویر را بسیار دقیق بررسی کن. '
    'فقط یک JSON معتبر با دقیقاً این فیلدها برگردان و هیچ متن دیگری ننویس: '
    '{"instagramId":"","fullName":"","phoneNumber":"","job":"","employeeCount":"","city":"","challenge":"","description":""}. '

    'ابتدا ساختار رابط کاربری تصویر را در ذهن خود تشخیص بده و سپس اطلاعات را استخراج کن. '
    'هر متن را بر اساس محل قرارگیری آن تشخیص بده: '
    'نام پروفایل، username یا instagramId، متن پیام، اطلاعات تماس، و سایر متن‌های رابط کاربری. '

    'قوانین عمومی: '
    'فقط اطلاعاتی را وارد کن که واقعاً در تصویر دیده می‌شود. '
    'هیچ چیز را حدس نزن، کامل نکن، اصلاح نکن یا از روی زمینه استنباط نکن. '
    'اگر فیلدی وجود ندارد یا درباره آن مطمئن نیستی، دقیقاً "" بگذار. '
    'دقت مهم‌تر از کامل بودن اطلاعات است. '
    'اگر بین دو مقدار مردد هستی، محتمل‌ترین مقدار را انتخاب نکن و آن فیلد را "" بگذار. '
    'متن‌های رابط کاربری مانند Save، Message، زمان، وضعیت آنلاین، دکمه‌ها، آیکون‌ها و برچسب‌های سیستمی را نادیده بگیر. '

    'قواعد instagramId: '
    'instagramId همان username یا handle حساب اینستاگرام است. '
    'instagramId لازم نیست با @ شروع شود. '
    'در صفحه چت اینستاگرام، متنی که در بالای صفحه و مستقیماً زیر نام مخاطب نمایش داده می‌شود معمولاً instagramId است. '
    'برای مثال اگر بالای صفحه نام Saeid Sa دیده شود و درست زیر آن hostail نوشته شده باشد، instagramId باید hostail باشد. '
    'متنی مانند hostail، ali123، company.name، user_name یا موارد مشابه اگر در محل username پروفایل دیده شود باید در instagramId قرار بگیرد. '
    'username را به هیچ عنوان به عنوان job ثبت نکن. '
    'اگر username واضح نیست یا مطمئن نیستی، instagramId را "" بگذار. '

    'قواعد fullName: '
    'fullName باید نام و نام خانوادگی شخص باشد، اگر به صورت واضح در پیام‌ها یا اطلاعات تماس دیده می‌شود. '
    'نام نمایشی پروفایل را فقط زمانی به fullName تبدیل کن که واضحاً نام شخص باشد. '
    'اگر نام کامل در متن پیام یا کارت تماس واضح‌تر دیده می‌شود، همان را ترجیح بده. '
    'نام کاربری یا username را در fullName قرار نده. '

    'قانون بسیار مهم phoneNumber: '
    'phoneNumber یک فیلد بسیار حساس است و حتی اشتباه در یک رقم قابل قبول نیست. '
    'شماره تماس را دقیقاً رقم‌به‌رقم و از چپ به راست از روی خود تصویر بخوان. '
    'پس از خواندن اولیه، بدون تکیه بر حافظه یا حدس، دوباره از ابتدای شماره تا انتها همه ارقام را با خود تصویر تطبیق بده. '
    'در بررسی دوم مخصوصاً ارقام مشابه یا قابل اشتباه مانند ۲ و ۷، ۳ و ۵، ۶ و ۸، ۰ و ۹ را با دقت بیشتری بررسی کن. '
    'ترتیب ارقام را دقیقاً حفظ کن و بررسی کن که هیچ دو رقم مجاور جابه‌جا نشده باشند. '
    'اگر شماره موبایل ایران است، فقط زمانی آن را برگردان که تمام 11 رقم آن واضح و بدون ابهام دیده شوند. '
    'اگر شماره بیش از یک بار در تصویر دیده می‌شود، همه نمونه‌ها را با هم مقایسه کن و فقط اگر با هم سازگار هستند شماره را برگردان. '
    'اگر حتی درباره یک رقم شک داری، یا دو خوانش متفاوت به دست می‌آید، phoneNumber را دقیقاً "" قرار بده. '
    'از الگوی رایج شماره موبایل، پیش‌شماره، اطلاعات قبلی، نام شخص، شهر، یا هر نوع حدس برای اصلاح شماره استفاده نکن. '
    'هیچ رقمی را اضافه، حذف، جابه‌جا، اصلاح، تکمیل یا حدس نزن. '
    'شماره را دقیقاً همان‌طور که در تصویر دیده می‌شود و با همان نوع ارقام فارسی یا لاتین برگردان. '

    'قواعد job: '
    'job فقط زمانی پر شود که شغل، سمت یا حوزه کاری شخص به صورت صریح در پیام‌ها یا اطلاعات تماس نوشته شده باشد. '
    'username، نام پیج، نام پروفایل، bio کوتاه، نام حساب یا متن زیر نام پروفایل شغل محسوب نمی‌شود. '
    'کلمات انگلیسی کوتاه را فقط به دلیل ظاهرشان شغل فرض نکن. '
    'اگر شغل به صورت صریح در تصویر نوشته نشده، job باید "" باشد. '

    'قواعد employeeCount: '
    'employeeCount فقط تعداد کارکنان، کارمندان یا پرسنل باشد. '
    'اگر تعداد پرسنل صریحاً نوشته نشده، employeeCount را "" بگذار. '

    'قواعد city: '
    'city فقط زمانی پر شود که نام شهر به صورت صریح در تصویر نوشته شده باشد. '
    'شهر را از شماره تلفن، نام کاربری، زبان، لهجه یا اطلاعات دیگر حدس نزن. '

    'قواعد challenge: '
    'challenge فقط مشکل، چالش، نیاز یا دغدغه‌ای باشد که شخص به صورت صریح در پیام‌های خودش بیان کرده است. '
    'اگر چالش مشخصی بیان نشده، challenge را "" بگذار. '

    'قواعد description: '
    'description فقط توضیحات اضافی مهمی باشد که شخص در پیام‌ها بیان کرده و در فیلدهای دیگر قرار نمی‌گیرد. '
    'اطلاعاتی که قبلاً در fullName، phoneNumber، job، employeeCount، city یا challenge قرار گرفته‌اند را در description تکرار نکن. '

    'کنترل نهایی قبل از پاسخ: '
    'بررسی کن که username اشتباهاً در job قرار نگرفته باشد. '
    'شماره تماس را یک بار دیگر مستقل از مقدار نوشته‌شده در پاسخ، مستقیماً با تصویر مقایسه کن. '
    'اگر در این کنترل نهایی حتی یک رقم شماره با تصویر تطابق قطعی ندارد، phoneNumber را "" قرار بده. '
    'بررسی کن که هیچ فیلدی بر اساس حدس پر نشده باشد. '
    'در نهایت فقط JSON معتبر را بدون Markdown، توضیح یا متن اضافه برگردان.'
)

def extract_json_from_text(text: str):
    text = (text or "").strip()

    if text.startswith("```json"):
        text = text[7:].strip()
    elif text.startswith("```"):
        text = text[3:].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        text = text[start:end + 1]

    return json.loads(text)


def clean_value(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return ""

    return str(value).strip()


def normalize_data(data):
    return {
        "instagramId": clean_value(data.get("instagramId")),
        "fullName": clean_value(data.get("fullName")),
        "phoneNumber": clean_value(data.get("phoneNumber")),
        "job": clean_value(data.get("job")),
        "employeeCount": clean_value(data.get("employeeCount")),
        "city": clean_value(data.get("city")),
        "challenge": clean_value(data.get("challenge")),
        "description": clean_value(data.get("description")),
    }


def image_file_to_base64(image_path: str):
    with open(image_path, "rb") as f:
        raw = f.read()

    encoded = base64.b64encode(raw).decode("utf-8")

    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"

    return encoded, mime_type


def set_command(command: dict):
    response = requests.post(
        f"{BRIDGE_URL}/set-command",
        json=command,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_result():
    response = requests.get(
        f"{BRIDGE_URL}/result",
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def wait_for_result(command_id: str, timeout: int = 180):
    start = time.time()

    while True:
        data = get_result()
        result = data.get("result")

        if result and result.get("id") == command_id:
            return result

        if time.time() - start > timeout:
            raise TimeoutError("Timed out waiting for vision result.")

        time.sleep(2)


def analyze_image_via_bridge(image_path: str):
    image_base64, mime_type = image_file_to_base64(image_path)

    command_id = str(uuid.uuid4())

    command = {
        "id": command_id,
        "type": "vision",
        "fileName": os.path.basename(image_path),
        "mimeType": mime_type,
        "imageBase64": image_base64,
        "prompt": VISION_PROMPT,
    }

    print("")
    print("SENDING COMMAND:")
    print(command_id)
    print("FILE:", image_path)
    print("")

    set_command(command)

    result = wait_for_result(command_id)

    print("RAW RESULT:")
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )
    print("")

    if not result.get("success"):
        raise RuntimeError(result.get("error", "Vision failed."))

    text = result.get("text", "")
    parsed = extract_json_from_text(text)
    normalized = normalize_data(parsed)

    return normalized


if __name__ == "__main__":
    image_path = r"C:\ChatGPTTelegram\test.jpg"

    data = analyze_image_via_bridge(image_path)

    print("NORMALIZED DATA:")
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        )
    )