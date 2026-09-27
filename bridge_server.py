import json
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


# =========================================================
# SERVER CONFIG
# =========================================================
#
# پروژه قدیمی فروش دوره:
#     127.0.0.1:8766
#
# Queue Version:
#     127.0.0.1:8767
#
# این دو عمداً از هم جدا هستند تا نسخه قدیمی در زمان
# توسعه نسخه جدید بدون توقف کار کند.
# =========================================================

HOST = "127.0.0.1"
PORT = 8767


# =========================================================
# STATE
# =========================================================

lock = threading.Lock()

state = {
    # فقط یک Vision command در هر لحظه باید برای Extension
    # قابل مشاهده باشد.
    "command": None,

    # نتیجه‌ها بر اساس command_id نگه‌داری می‌شوند.
    #
    # این بخش باعث می‌شود نتیجه دیررس یک command قدیمی
    # نتیجه command جدید را خراب نکند.
    "results": OrderedDict(),
}


MAX_RESULTS = 100


# =========================================================
# HELPERS
# =========================================================

def _command_id(value):

    if not isinstance(
        value,
        dict
    ):
        return None

    command_id = value.get(
        "id"
    )

    if command_id is None:
        return None

    command_id = str(
        command_id
    ).strip()

    if not command_id:
        return None

    return command_id


def _store_result(result):

    result_id = _command_id(
        result
    )

    if not result_id:
        return None

    state["results"][
        result_id
    ] = result

    state["results"].move_to_end(
        result_id
    )

    while (
        len(state["results"])
        > MAX_RESULTS
    ):

        state["results"].popitem(
            last=False
        )

    return result_id


def _same_command(
    left,
    right
):

    if not isinstance(left, dict):
        return False

    if not isinstance(right, dict):
        return False

    return left == right


# =========================================================
# HTTP HANDLER
# =========================================================

class Handler(
    BaseHTTPRequestHandler
):

    # =====================================================
    # RESPONSE
    # =====================================================

    def _send_json(
        self,
        status_code,
        data
    ):

        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode(
            "utf-8"
        )

        self.send_response(
            status_code
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.send_header(
            "Content-Length",
            str(
                len(body)
            )
        )

        self.end_headers()

        self.wfile.write(
            body
        )


    # =====================================================
    # OPTIONS / CORS
    # =====================================================

    def do_OPTIONS(
        self
    ):

        self.send_response(
            204
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.end_headers()


    # =====================================================
    # GET
    # =====================================================

    def do_GET(
        self
    ):

        parsed = urlparse(
            self.path
        )

        path = parsed.path

        query = parse_qs(
            parsed.query
        )


        # -------------------------------------------------
        # HEALTH
        # -------------------------------------------------

        if path == "/health":

            with lock:

                active_id = _command_id(
                    state["command"]
                )

                result_count = len(
                    state["results"]
                )

            self._send_json(
                200,
                {
                    "success": True,
                    "status": "ready",
                    "activeCommandId": active_id,
                    "storedResultCount": result_count,
                    "port": PORT,
                    "requestIdAware": True,
                    "lateResultProtection": True,
                    "idempotentCommandSubmit": True,
                }
            )

            return


        # -------------------------------------------------
        # COMMAND
        #
        # Chrome Extension این endpoint را poll می‌کند.
        # -------------------------------------------------

        if path == "/command":

            with lock:

                command = state[
                    "command"
                ]

            self._send_json(
                200,
                {
                    "success": True,
                    "command": command,
                }
            )

            return


        # -------------------------------------------------
        # RESULT
        #
        # Client باید:
        #
        #     /result?id=<command_id>
        #
        # را درخواست کند.
        #
        # fallback بدون id برای backward compatibility
        # باقی مانده است.
        # -------------------------------------------------

        if path == "/result":

            requested_id = (
                query.get(
                    "id",
                    [None]
                )[0]
                or
                query.get(
                    "requestId",
                    [None]
                )[0]
            )

            if requested_id is not None:

                requested_id = str(
                    requested_id
                ).strip()

            with lock:

                if requested_id:

                    result = (
                        state["results"]
                        .get(
                            requested_id
                        )
                    )

                else:

                    if state["results"]:

                        result = next(
                            reversed(
                                state["results"]
                                .values()
                            )
                        )

                    else:

                        result = None

            self._send_json(
                200,
                {
                    "success": True,
                    "requestedId": requested_id,
                    "result": result,
                }
            )

            return


        # -------------------------------------------------
        # NOT FOUND
        # -------------------------------------------------

        self._send_json(
            404,
            {
                "success": False,
                "error": "Not found",
            }
        )


    # =====================================================
    # POST
    # =====================================================

    def do_POST(
        self
    ):

        parsed = urlparse(
            self.path
        )

        path = parsed.path


        # -------------------------------------------------
        # READ BODY
        # -------------------------------------------------

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

        except Exception:

            self._send_json(
                400,
                {
                    "success": False,
                    "error": "Invalid Content-Length",
                }
            )

            return


        if content_length <= 0:

            self._send_json(
                400,
                {
                    "success": False,
                    "error": "Empty request body",
                }
            )

            return


        raw = self.rfile.read(
            content_length
        )


        # -------------------------------------------------
        # JSON
        # -------------------------------------------------

        try:

            data = json.loads(
                raw.decode(
                    "utf-8"
                )
            )

        except Exception:

            self._send_json(
                400,
                {
                    "success": False,
                    "error": "Invalid JSON",
                }
            )

            return


        if not isinstance(
            data,
            dict
        ):

            self._send_json(
                400,
                {
                    "success": False,
                    "error": "JSON body must be an object",
                }
            )

            return


        # =================================================
        # SET COMMAND
        # =================================================
        #
        # مهم‌ترین بخش این نسخه:
        #
        # ارسال دوباره همان command_id نباید:
        #
        # - Result موجود را حذف کند
        # - Command دیگری را overwrite کند
        # - Vision را دوباره بی‌دلیل اجرا کند
        #
        # =================================================

        if path == "/set-command":

            command_id = _command_id(
                data
            )

            if not command_id:

                self._send_json(
                    400,
                    {
                        "success": False,
                        "error": "Command id is required",
                    }
                )

                return


            with lock:

                active_command = state[
                    "command"
                ]

                active_id = _command_id(
                    active_command
                )

                existing_result = (
                    state["results"]
                    .get(
                        command_id
                    )
                )


                # =========================================
                # CASE 1:
                # این command قبلاً تمام شده و Result آن
                # موجود است.
                #
                # ممکن است POST قبلی موفق بوده اما پاسخ HTTP
                # به Client نرسیده باشد.
                #
                # Result را هرگز حذف نکن.
                # =========================================

                if existing_result is not None:

                    response_payload = {
                        "success": True,
                        "commandId": command_id,
                        "state": "already_completed",
                        "alreadyCompleted": True,
                        "alreadyActive": False,
                    }

                    response_code = 200


                # =========================================
                # CASE 2:
                # همان command هنوز Active است.
                #
                # Retry همان request است.
                # دوباره queue نمی‌شود.
                # =========================================

                elif active_id == command_id:

                    if not _same_command(
                        active_command,
                        data
                    ):

                        response_payload = {
                            "success": False,
                            "error": (
                                "COMMAND_ID_REUSED_WITH_DIFFERENT_PAYLOAD"
                            ),
                            "commandId": command_id,
                        }

                        response_code = 409

                    else:

                        response_payload = {
                            "success": True,
                            "commandId": command_id,
                            "state": "already_active",
                            "alreadyCompleted": False,
                            "alreadyActive": True,
                        }

                        response_code = 200


                # =========================================
                # CASE 3:
                # یک command دیگر هنوز Active است.
                #
                # command جدید حق ندارد آن را overwrite کند.
                #
                # در Queue Version اصولاً نباید رخ دهد چون
                # Vision worker سریالی است؛ ولی این guard
                # جلوی race/corruption را می‌گیرد.
                # =========================================

                elif active_id:

                    response_payload = {
                        "success": False,
                        "error": "BRIDGE_BUSY",
                        "activeCommandId": active_id,
                        "requestedCommandId": command_id,
                    }

                    response_code = 409


                # =========================================
                # CASE 4:
                # Bridge آزاد است.
                # command جدید ثبت می‌شود.
                # =========================================

                else:

                    state["command"] = data

                    response_payload = {
                        "success": True,
                        "commandId": command_id,
                        "state": "accepted",
                        "alreadyCompleted": False,
                        "alreadyActive": False,
                    }

                    response_code = 200


            if response_code == 200:

                state_name = response_payload.get(
                    "state"
                )

                print("")
                print(
                    "========================================"
                )
                print(
                    "COMMAND SUBMIT"
                )
                print(
                    "COMMAND ID:",
                    command_id
                )
                print(
                    "STATE:",
                    state_name
                )

                if state_name == "accepted":

                    print(
                        "FILE:",
                        data.get(
                            "fileName",
                            "-"
                        )
                    )

                print(
                    "========================================"
                )
                print("")

            else:

                print("")
                print(
                    "BRIDGE COMMAND REJECTED"
                )
                print(
                    json.dumps(
                        response_payload,
                        ensure_ascii=False,
                        indent=2
                    )
                )
                print("")


            self._send_json(
                response_code,
                response_payload
            )

            return


        # =================================================
        # SET RESULT
        # =================================================
        #
        # Extension نتیجه Vision را اینجا POST می‌کند.
        #
        # نتیجه همیشه با ID خودش ذخیره می‌شود.
        #
        # command فعال فقط وقتی پاک می‌شود که result_id
        # دقیقاً برابر activeCommandId باشد.
        # =================================================

        if path == "/set-result":

            result_id = _command_id(
                data
            )

            if not result_id:

                self._send_json(
                    400,
                    {
                        "success": False,
                        "error": "Result id is required",
                    }
                )

                return


            with lock:

                active_id_before = (
                    _command_id(
                        state["command"]
                    )
                )


                # اگر Extension به علت retry همان Result را
                # دوباره ارسال کرد، همان ID update می‌شود.
                # Result دیگری تحت تأثیر قرار نمی‌گیرد.

                _store_result(
                    data
                )


                if (
                    active_id_before
                    == result_id
                ):

                    state[
                        "command"
                    ] = None

                    cleared_active = True

                else:

                    cleared_active = False


            print("")
            print(
                "========================================"
            )
            print(
                "RESULT RECEIVED"
            )
            print(
                "RESULT ID:",
                result_id
            )
            print(
                "ACTIVE BEFORE:",
                active_id_before
            )
            print(
                "ACTIVE CLEARED:",
                cleared_active
            )

            if (
                active_id_before
                and
                active_id_before
                != result_id
            ):

                print(
                    "LATE/STALE RESULT STORED "
                    "WITHOUT CLEARING CURRENT COMMAND"
                )

            print(
                "========================================"
            )
            print("")


            self._send_json(
                200,
                {
                    "success": True,
                    "resultId": result_id,
                    "clearedActiveCommand": (
                        cleared_active
                    ),
                    "activeCommandIdBefore": (
                        active_id_before
                    ),
                }
            )

            return


        # =================================================
        # UNKNOWN POST
        # =================================================

        self._send_json(
            404,
            {
                "success": False,
                "error": "Not found",
            }
        )


    # =====================================================
    # SILENCE DEFAULT HTTP LOG
    # =====================================================

    def log_message(
        self,
        format,
        *args
    ):

        return


# =========================================================
# SERVER
# =========================================================

class BridgeHTTPServer(
    ThreadingHTTPServer
):

    allow_reuse_address = True

    daemon_threads = True


# =========================================================
# MAIN
# =========================================================

def main():

    server = BridgeHTTPServer(
        (
            HOST,
            PORT
        ),
        Handler
    )

    print("")
    print(
        "========================================"
    )
    print(
        "       ChatGPT Queue Bridge"
    )
    print(
        "========================================"
    )
    print("")

    print(
        f"Running on http://{HOST}:{PORT}"
    )

    print("")
    print(
        "Result storage: REQUEST-ID AWARE"
    )
    print(
        "Late-result protection: ENABLED"
    )
    print(
        "Idempotent command submit: ENABLED"
    )
    print(
        "Active-command overwrite: BLOCKED"
    )
    print(
        f"Max stored results: {MAX_RESULTS}"
    )
    print("")

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print("")
        print(
            "Bridge stopped by user."
        )

    finally:

        server.server_close()


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()