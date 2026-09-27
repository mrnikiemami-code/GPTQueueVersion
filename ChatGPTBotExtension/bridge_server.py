import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = "127.0.0.1"
PORT = 8766

lock = threading.Lock()

state = {
    "command": None,
    "result": None
}


class Handler(BaseHTTPRequestHandler):

    def _send_json(self, status_code, data):
        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status_code)
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
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
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
        self.end_headers()

    def do_GET(self):

        if self.path == "/health":
            self._send_json(
                200,
                {
                    "success": True,
                    "status": "ready"
                }
            )
            return

        if self.path == "/command":
            with lock:
                command = state["command"]

            self._send_json(
                200,
                {
                    "success": True,
                    "command": command
                }
            )
            return

        if self.path == "/result":
            with lock:
                result = state["result"]

            self._send_json(
                200,
                {
                    "success": True,
                    "result": result
                }
            )
            return

        self._send_json(
            404,
            {
                "success": False,
                "error": "Not found"
            }
        )

    def do_POST(self):
        content_length = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        raw = self.rfile.read(
            content_length
        )

        try:
            data = json.loads(
                raw.decode("utf-8")
            )
        except Exception:
            self._send_json(
                400,
                {
                    "success": False,
                    "error": "Invalid JSON"
                }
            )
            return

        if self.path == "/set-command":

            with lock:
                state["command"] = data
                state["result"] = None

            print("")
            print("COMMAND SET:")
            print(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2
                )
            )
            print("")

            self._send_json(
                200,
                {
                    "success": True
                }
            )
            return

        if self.path == "/set-result":

            with lock:
                state["result"] = data
                state["command"] = None

            print("")
            print("RESULT RECEIVED:")
            print(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2
                )
            )
            print("")

            self._send_json(
                200,
                {
                    "success": True
                }
            )
            return

        self._send_json(
            404,
            {
                "success": False,
                "error": "Not found"
            }
        )

    def log_message(self, format, *args):
        return


def main():

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler
    )

    print("")
    print("========================================")
    print("     ChatGPT Telegram Bridge")
    print("========================================")
    print("")
    print(
        f"Running on http://{HOST}:{PORT}"
    )
    print("")

    server.serve_forever()


if __name__ == "__main__":
    main()