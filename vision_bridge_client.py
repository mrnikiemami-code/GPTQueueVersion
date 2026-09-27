import os
import time
import json
import uuid
import base64
import mimetypes
import requests


# =========================================================
# BRIDGE CONFIG
# =========================================================
#
# IMPORTANT:
# پروژه قدیمی فروش دوره روی 8766 باقی می‌ماند.
# Queue Version از Bridge مستقل روی 8767 استفاده می‌کند.
# =========================================================

BRIDGE_URL = "http://127.0.0.1:8767"

BRIDGE_CONNECT_TIMEOUT_SECONDS = 5
BRIDGE_REQUEST_READ_TIMEOUT_SECONDS = 30

VISION_RESULT_TIMEOUT_SECONDS = 180
VISION_RESULT_POLL_INTERVAL_SECONDS = 2

# اگر POST فرمان به دلیل قطع لحظه‌ای شبکه Local شکست خورد،
# همان command_id دوباره ارسال می‌شود.
BRIDGE_COMMAND_SEND_ATTEMPTS = 3
BRIDGE_COMMAND_RETRY_DELAY_SECONDS = 1


# =========================================================
# VISION PROMPT
# =========================================================
#
# این Prompt همان Prompt فعلی و تست‌شده است.
# عمداً تغییر داده نشده است.
# =========================================================

VISION_PROMPT = (
    'این تصویر را بسیار دقیق بررسی کن. '
    'فقط یک JSON معتبر با دقیقاً این فیلدها برگردان و هیچ متن دیگری ننویس: '
    '{"instagramId":"","fullName":"","phoneNumber":"","job":"","employeeCount":"","city":"","challenge":"","description":""}. '

    'ابتدا ساختار رابط کاربری تصویر را در ذهن خود تشخیص بده و سپس اطلاعات را استخراج کن. '
    'هر متن را بر اساس محل قرارگیری آن تشخیص بده: '
    'نام پروفایل، username یا instagramId، متن پیام، اطلاعات تماس، کارت شماره تلفن و سایر متن‌های رابط کاربری. '

    'قوانین عمومی: '
    'فقط اطلاعاتی را وارد کن که واقعاً در تصویر دیده می‌شود. '
    'هیچ چیز را حدس نزن، کامل نکن یا از روی زمینه استنباط نکن. '
    'اگر فیلدی وجود ندارد یا درباره آن مطمئن نیستی، دقیقاً "" بگذار. '
    'دقت مهم‌تر از کامل بودن اطلاعات است. '
    'اگر بین دو مقدار مردد هستی، محتمل‌ترین مقدار را انتخاب نکن و آن فیلد را "" بگذار. '
    'متن‌های رابط کاربری مانند Save، Message، زمان، وضعیت آنلاین، دکمه‌ها، آیکون‌ها و برچسب‌های سیستمی را نادیده بگیر، '
    'مگر اینکه داخل یک کارت تماس، شماره تلفن واقعی مخاطب نمایش داده شده باشد. '

    'قواعد instagramId: '
    'instagramId همان username یا handle حساب اینستاگرام است. '
    'instagramId لازم نیست با @ شروع شود. '
    'در صفحه چت اینستاگرام، متنی که در بالای صفحه و مستقیماً زیر نام مخاطب نمایش داده می‌شود معمولاً instagramId است. '
    'برای مثال اگر بالای صفحه نام Saeid Sa دیده شود و درست زیر آن hostail نوشته شده باشد، instagramId باید hostail باشد. '
    'متنی مانند hostail، ali123، company.name یا user_name اگر در محل username پروفایل دیده شود باید در instagramId قرار بگیرد. '
    'username را به هیچ عنوان به عنوان job ثبت نکن. '
    'اگر username واضح نیست یا مطمئن نیستی، instagramId را "" بگذار. '

    'قواعد fullName: '
    'fullName باید نام و نام خانوادگی شخص باشد، اگر به صورت واضح در پیام‌ها یا اطلاعات تماس دیده می‌شود. '
    'اگر فقط نام کوچک به صورت واضح دیده می‌شود و نام خانوادگی وجود ندارد، همان نام کوچک را در fullName قرار بده. '
    'نام نمایشی پروفایل را فقط زمانی به fullName تبدیل کن که واضحاً نام شخص باشد. '
    'اگر نام داخل پیام واضح‌تر از نام پروفایل است، متن پیام را ترجیح بده. '
    'نام کاربری یا username را در fullName قرار نده. '

    'قانون بسیار مهم phoneNumber: '
    'phoneNumber یک فیلد بسیار حساس است و حتی اشتباه در یک رقم قابل قبول نیست. '
    'شماره تماس را فقط از متن واضح داخل تصویر استخراج کن. '
    'هیچ رقم را از روی الگوی رایج شماره موبایل، پیش‌شماره، اطلاعات قبلی، شهر، نام شخص یا زمینه حدس نزن. '

    'شماره را همیشه از ابتدای شماره تا انتهای آن، دقیقاً مطابق جایگاه واقعی ارقام در تصویر بخوان. '
    'هیچ مرحله‌ای از بررسی نباید باعث جابه‌جایی، بازچینی یا تغییر ترتیب ارقام شود. '
    'هر رقم فقط متعلق به همان موقعیتی است که در تصویر دیده می‌شود. '
    'هیچ دو رقم مجاور را جابه‌جا نکن. '

    'برای بررسی دقت، می‌توانی شماره را فقط به صورت ذهنی به گروه‌های 2 یا 3 رقمی تقسیم کنی، '
    'اما این گروه‌بندی فقط برای کنترل است و حق نداری بر اساس گروه‌ها شماره را دوباره مرتب یا بازسازی کنی. '

    'برای phoneNumber همیشه خروجی نهایی را با ارقام لاتین 0 تا 9 برگردان. '
    'اگر شماره در تصویر با ارقام فارسی یا عربی نوشته شده است، فقط شکل رقم را به لاتین تبدیل کن. '
    'این تبدیل فقط تغییر نوع نمایش رقم است و نباید مقدار، ترتیب یا تعداد ارقام را تغییر دهد. '

    'قانون بسیار مهم تشخیص ارقام فارسی: '
    'اگر شماره با ارقام فارسی نوشته شده است، ابتدا هر رقم را بر اساس شکل واقعی همان رقم فارسی در تصویر تشخیص بده. '
    'قبل از تبدیل به لاتین، شکل خود رقم فارسی را بررسی کن. '
    'مخصوصاً جفت‌های قابل اشتباه زیر را با دقت جداگانه بررسی کن: '
    '۲ و ۳، ۲ و ۷، ۳ و ۵، ۴ و ۶، ۵ و ۹، ۶ و ۸، ۰ و ۹. '

    'برای تشخیص ۲ و ۳ فارسی، به شکل واقعی کاراکتر در تصویر توجه کن و از جایگاه رقم در شماره برای حدس استفاده نکن. '
    'اگر یک رقم می‌تواند ۲ یا ۳ باشد و شکل آن قطعی نیست، یکی را انتخاب نکن و کل phoneNumber را "" بگذار. '
    'همین قانون برای تمام جفت‌های مشابه دیگر نیز برقرار است. '

    'اگر کیفیت تصویر اجازه تشخیص قطعی یک رقم را نمی‌دهد، آن رقم را حدس نزن. '
    'اگر حتی یک رقم مبهم، تار، کوچک، پوشیده یا دوپهلو است، phoneNumber را دقیقاً "" قرار بده. '

    'قواعد فرمت شماره موبایل ایران: '
    'اگر شماره به صورت 09xxxxxxxxx دیده شد، همان شماره را با 11 رقم لاتین برگردان. '
    'اگر شماره به صورت +98xxxxxxxxxx دیده شد، فقط +98 را به 0 تبدیل کن و خروجی را به شکل 09xxxxxxxxx برگردان. '
    'اگر شماره به صورت 0098xxxxxxxxxx دیده شد، فقط 0098 را به 0 تبدیل کن و خروجی را به شکل 09xxxxxxxxx برگردان. '
    'هیچ تغییر دیگری در ارقام انجام نده. '

    'مثال: '
    'اگر 0910 606 5805 و +98 910 606 5805 هر دو در تصویر دیده شوند، '
    'این دو یک شماره محسوب می‌شوند و خروجی باید 09106065805 باشد. '

    'فاصله، خط تیره، پرانتز و جداکننده‌های ظاهری داخل شماره را در خروجی حذف کن. '
    'اما حذف جداکننده‌ها نباید باعث تغییر ترتیب یا مقدار ارقام شود. '

    'اگر شماره موبایل ایران است، بعد از نرمال‌سازی باید دقیقاً 11 رقم داشته باشد و با 09 شروع شود. '
    'اگر تعداد ارقام با این ساختار سازگار نیست، شماره را اصلاح یا تکمیل نکن؛ phoneNumber را "" بگذار. '

    'اگر شماره بیش از یک بار در تصویر دیده می‌شود، نمونه‌ها را با هم مقایسه کن. '
    'فرمت 09xxxxxxxxx، +98xxxxxxxxxx و 0098xxxxxxxxxx را فقط از نظر کد کشور معادل یکدیگر در نظر بگیر. '
    'پس از نرمال‌سازی کد کشور، تمام ارقام باید موقعیت‌به‌موقعیت دقیقاً یکسان باشند. '
    'اگر حتی یک رقم بین دو نمونه متفاوت است، phoneNumber را "" بگذار. '

    'اگر شماره فقط یک بار در تصویر دیده می‌شود، نبود نمونه دوم دلیل خالی گذاشتن phoneNumber نیست. '
    'در این حالت اگر تمام ارقام واضح و قطعی هستند، شماره را برگردان. '

    'برای phoneNumber دو بار خوانش مستقل انجام بده. '
    'در خوانش اول، شماره را مستقیماً از تصویر از ابتدا تا انتها بخوان. '
    'سپس نتیجه خوانش اول را موقتاً کنار بگذار. '
    'در خوانش دوم دوباره مستقیماً از خود تصویر شماره را از ابتدا تا انتها بخوان. '
    'خوانش دوم نباید بر اساس حافظه خوانش اول انجام شود. '

    'سپس دو خوانش را موقعیت‌به‌موقعیت مقایسه کن: '
    'رقم 1 با رقم 1، رقم 2 با رقم 2 و به همین ترتیب تا آخر. '
    'اگر حتی یک موقعیت متفاوت است، phoneNumber را "" بگذار. '
    'اگر تعداد ارقام دو خوانش متفاوت است، phoneNumber را "" بگذار. '

    'اگر در یکی از خوانش‌ها رقم فارسی ۲ دیده شده و در دیگری احتمال ۳ وجود دارد، phoneNumber را "" بگذار. '
    'اگر درباره یکی از ارقام مشابه مانند ۲/۳، ۲/۷، ۳/۵، ۴/۶، ۵/۹، ۶/۸ یا ۰/۹ تردید وجود دارد، phoneNumber را "" بگذار. '

    'اگر شماره هم در متن پیام و هم در کارت Phone number دیده می‌شود، هر دو را بخوان. '
    'اگر یکی با 09 و دیگری با +98 نمایش داده شده است، ابتدا فقط کد کشور را نرمال کن و سپس ارقام را مقایسه کن. '
    'اگر بعد از این نرمال‌سازی دقیقاً برابر هستند، شماره معتبر است. '
    'اگر متفاوت هستند، phoneNumber را "" بگذار. '

    'قواعد job: '
    'job فقط زمانی پر شود که شغل، سمت یا حوزه کاری شخص به صورت صریح در پیام‌های خودش یا اطلاعات واضح مخاطب نوشته شده باشد. '
    'username، نام پیج، نام پروفایل یا متن زیر نام پروفایل به تنهایی شغل محسوب نمی‌شود. '
    'کلمات انگلیسی کوتاه را فقط به دلیل ظاهرشان شغل فرض نکن. '
    'اگر شغل به صورت صریح نوشته نشده، job باید "" باشد. '

    'اگر شخص چند عبارت مرتبط با کار خودش نوشته است، مانند حوزه کاری و جایگاه شغلی، '
    'می‌توانی آن‌ها را در job با یک جداکننده مناسب و بدون افزودن اطلاعات جدید قرار بدهی. '

    'قواعد employeeCount: '
    'employeeCount فقط تعداد کارکنان، کارمندان یا پرسنل باشد. '
    'اگر کاربر در پاسخ به سؤال تعداد پرسنل فقط یک عدد نوشته است و موقعیت گفتگو واضحاً نشان می‌دهد آن عدد پاسخ تعداد پرسنل است، همان عدد را ثبت کن. '
    'اگر معلوم نیست عدد مربوط به پرسنل است، employeeCount را "" بگذار. '

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
    'بررسی کن که هیچ فیلدی بر اساس حدس پر نشده باشد. '

    'برای phoneNumber یک کنترل نهایی بسیار سخت‌گیرانه انجام بده: '
    'شماره نهایی باید حاصل تشخیص مستقیم ارقام تصویر باشد، نه اصلاح بر اساس الگو. '
    'موقعیت و ترتیب تمام ارقام باید با تصویر تطابق داشته باشد. '
    'اگر شماره فارسی بوده، دوباره شکل ارقام حساس مخصوصاً ۲ و ۳ را بررسی کن. '
    'اگر حتی یک رقم قطعیت کافی ندارد، phoneNumber را "" بگذار. '

    'اما اگر تمام ارقام واضح هستند، فقط به دلیل تفاوت فرمت 09 و +98 شماره را خالی نگذار. '
    'این دو فرمت را طبق قانون بالا نرمال و مقایسه کن. '

    'در نهایت فقط JSON معتبر را بدون Markdown، توضیح یا متن اضافه برگردان.'
)


# =========================================================
# JSON EXTRACTION
# =========================================================

def extract_json_from_text(text):

    # گاهی Bridge/Extension ممکن است مستقیماً object برگرداند.
    if isinstance(text, dict):
        return text

    if text is None:
        raise ValueError(
            "Vision result text is empty."
        )

    if not isinstance(text, str):
        raise ValueError(
            f"Vision result text has invalid type: {type(text).__name__}"
        )

    text = text.strip()

    if not text:
        raise ValueError(
            "Vision returned empty text."
        )

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

    parsed = json.loads(
        text
    )

    if not isinstance(parsed, dict):
        raise ValueError(
            "Vision JSON root must be an object."
        )

    return parsed


# =========================================================
# NORMALIZATION
# =========================================================

def clean_value(value):

    if value is None:
        return ""

    if isinstance(
        value,
        (dict, list)
    ):
        return ""

    return str(value).strip()


def normalize_data(data):

    if not isinstance(data, dict):
        raise ValueError(
            "Vision data must be a JSON object."
        )

    return {
        "instagramId": clean_value(
            data.get("instagramId")
        ),

        "fullName": clean_value(
            data.get("fullName")
        ),

        "phoneNumber": clean_value(
            data.get("phoneNumber")
        ),

        "job": clean_value(
            data.get("job")
        ),

        "employeeCount": clean_value(
            data.get("employeeCount")
        ),

        "city": clean_value(
            data.get("city")
        ),

        "challenge": clean_value(
            data.get("challenge")
        ),

        "description": clean_value(
            data.get("description")
        ),
    }


# =========================================================
# IMAGE
# =========================================================

def image_file_to_base64(
    image_path: str
):

    if not image_path:
        raise ValueError(
            "Image path is empty."
        )

    if not os.path.isfile(
        image_path
    ):
        raise FileNotFoundError(
            f"Image file does not exist: {image_path}"
        )

    file_size = os.path.getsize(
        image_path
    )

    if file_size <= 0:
        raise ValueError(
            f"Image file is empty: {image_path}"
        )

    with open(
        image_path,
        "rb"
    ) as f:

        raw = f.read()

    encoded = base64.b64encode(
        raw
    ).decode(
        "utf-8"
    )

    mime_type, _ = mimetypes.guess_type(
        image_path
    )

    if not mime_type:
        mime_type = "image/jpeg"

    return (
        encoded,
        mime_type
    )


# =========================================================
# BRIDGE REQUESTS
# =========================================================

def set_command(
    command: dict
):

    if not isinstance(
        command,
        dict
    ):
        raise ValueError(
            "Bridge command must be a dict."
        )

    command_id = str(
        command.get("id") or ""
    ).strip()

    if not command_id:
        raise ValueError(
            "Bridge command id is missing."
        )

    last_error = None

    for attempt in range(
        1,
        BRIDGE_COMMAND_SEND_ATTEMPTS + 1
    ):

        try:

            response = requests.post(
                f"{BRIDGE_URL}/set-command",
                json=command,
                timeout=(
                    BRIDGE_CONNECT_TIMEOUT_SECONDS,
                    BRIDGE_REQUEST_READ_TIMEOUT_SECONDS
                )
            )

            response.raise_for_status()

            try:
                result = response.json()

            except Exception as ex:
                raise RuntimeError(
                    "Bridge /set-command returned invalid JSON."
                ) from ex

            if not isinstance(
                result,
                dict
            ):
                raise RuntimeError(
                    "Bridge /set-command returned invalid response type."
                )

            return result

        except requests.RequestException as ex:

            last_error = ex

            print("")
            print(
                "BRIDGE SET-COMMAND NETWORK ERROR "
                f"({attempt}/{BRIDGE_COMMAND_SEND_ATTEMPTS})"
            )
            print(
                "COMMAND ID:",
                command_id
            )
            print(
                "ERROR:",
                str(ex)
            )
            print("")

            if (
                attempt <
                BRIDGE_COMMAND_SEND_ATTEMPTS
            ):
                time.sleep(
                    BRIDGE_COMMAND_RETRY_DELAY_SECONDS
                )

    raise RuntimeError(
        "Could not send Vision command to Bridge. "
        f"command_id={command_id}. "
        f"last_error={last_error}"
    )


def get_result(
    command_id: str
):

    command_id = str(
        command_id or ""
    ).strip()

    if not command_id:
        raise ValueError(
            "command_id is required."
        )

    response = requests.get(
        f"{BRIDGE_URL}/result",
        params={
            "id": command_id
        },
        timeout=(
            BRIDGE_CONNECT_TIMEOUT_SECONDS,
            BRIDGE_REQUEST_READ_TIMEOUT_SECONDS
        )
    )

    response.raise_for_status()

    try:

        result = response.json()

    except Exception as ex:

        raise RuntimeError(
            "Bridge /result returned invalid JSON."
        ) from ex

    if not isinstance(
        result,
        dict
    ):
        raise RuntimeError(
            "Bridge /result returned invalid response type."
        )

    return result


# =========================================================
# WAIT FOR EXACT COMMAND RESULT
# =========================================================

def wait_for_result(
    command_id: str,
    timeout: int = VISION_RESULT_TIMEOUT_SECONDS
):

    command_id = str(
        command_id or ""
    ).strip()

    if not command_id:
        raise ValueError(
            "command_id is required."
        )

    start = time.monotonic()

    last_network_error = None

    while True:

        elapsed = (
            time.monotonic()
            - start
        )

        if elapsed >= timeout:

            error_suffix = ""

            if last_network_error:
                error_suffix = (
                    " Last Bridge error: "
                    + str(last_network_error)
                )

            raise TimeoutError(
                "Timed out waiting for Vision result "
                f"for command {command_id} "
                f"after {timeout} seconds."
                + error_suffix
            )

        try:

            data = get_result(
                command_id
            )

            last_network_error = None

        except requests.RequestException as ex:

            # Bridge ممکن است برای لحظه‌ای در دسترس نباشد.
            # Job از بین نمی‌رود و تا پایان timeout صبر می‌کنیم.
            last_network_error = ex

            print("")
            print(
                "BRIDGE RESULT POLL NETWORK WARNING"
            )
            print(
                "COMMAND ID:",
                command_id
            )
            print(
                "ERROR:",
                str(ex)
            )
            print("")

            time.sleep(
                VISION_RESULT_POLL_INTERVAL_SECONDS
            )

            continue

        result = data.get(
            "result"
        )

        if isinstance(
            result,
            dict
        ):

            result_id = str(
                result.get("id") or ""
            ).strip()

            # فقط نتیجه همان command پذیرفته می‌شود.
            if result_id == command_id:

                return result

            # نتیجه متعلق به command دیگری هرگز پذیرفته نمی‌شود.
            if result_id:

                print("")
                print(
                    "IGNORING RESULT FOR DIFFERENT COMMAND"
                )
                print(
                    "WAITING FOR:",
                    command_id
                )
                print(
                    "RECEIVED:",
                    result_id
                )
                print("")

        time.sleep(
            VISION_RESULT_POLL_INTERVAL_SECONDS
        )


# =========================================================
# MAIN VISION CALL
# =========================================================

def analyze_image_via_bridge(
    image_path: str
):

    image_base64, mime_type = (
        image_file_to_base64(
            image_path
        )
    )

    command_id = str(
        uuid.uuid4()
    )

    command = {
        "id": command_id,
        "type": "vision",
        "fileName": os.path.basename(
            image_path
        ),
        "mimeType": mime_type,
        "imageBase64": image_base64,
        "prompt": VISION_PROMPT,
    }

    print("")
    print(
        "========================================"
    )
    print(
        "SENDING VISION COMMAND"
    )
    print(
        "COMMAND ID:",
        command_id
    )
    print(
        "FILE:",
        image_path
    )
    print(
        "BRIDGE:",
        BRIDGE_URL
    )
    print(
        "========================================"
    )
    print("")

    # اگر پاسخ HTTP ارسال command گم شود،
    # set_command همان command_id را retry می‌کند.
    #
    # bridge_server جدید باید set-command با ID یکسان
    # را idempotent مدیریت کند.
    set_command(
        command
    )

    result = wait_for_result(
        command_id,
        timeout=VISION_RESULT_TIMEOUT_SECONDS
    )

    print("")
    print(
        "RAW VISION RESULT:"
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )
    print("")

    result_id = str(
        result.get("id") or ""
    ).strip()

    if result_id != command_id:

        raise RuntimeError(
            "Bridge returned result for wrong command. "
            f"Expected={command_id}, "
            f"Received={result_id}"
        )

    if not result.get(
        "success"
    ):

        raise RuntimeError(
            result.get(
                "error",
                "Vision failed."
            )
        )

    text = result.get(
        "text",
        ""
    )

    parsed = extract_json_from_text(
        text
    )

    normalized = normalize_data(
        parsed
    )

    print("")
    print(
        "NORMALIZED VISION DATA:"
    )
    print(
        json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2
        )
    )
    print("")

    return normalized


# =========================================================
# MANUAL TEST
# =========================================================

if __name__ == "__main__":

    image_path = (
        r"C:\ChatGPTQueueVersion\test.jpg"
    )

    data = analyze_image_via_bridge(
        image_path
    )

    print(
        "NORMALIZED DATA:"
    )

    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        )
    )