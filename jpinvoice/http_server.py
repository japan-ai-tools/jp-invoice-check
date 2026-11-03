"""MCP over HTTP (streamable HTTP, JSON responses only). Python standard library only.

The stdio server (jpinvoice.server) is still the main way to run the checker on your own PC.
This module lets the same tools be reached over HTTP, for agents that only connect to remote MCP
servers (for example xAI remote MCP, Meta Muse) and for hosting on Apify (Standby mode).

Start:
    python -m jpinvoice.http_server [--host 127.0.0.1] [--port 8765] [--policy company_policy.json]

- POST /mcp   one JSON-RPC message (or a batch). Answers with application/json. Notifications get 202.
- GET  /      200 "ok" (health / Apify readiness probe).
- Anything else: 404 / 405. Bodies over 1 MB: 413. No sessions, no state between calls.
- Binds to 127.0.0.1 unless you pass --host. On Apify the port comes from ACTOR_WEB_SERVER_PORT and the host is 0.0.0.0.

Paid use on Apify (optional, see jpinvoice.apify_charge): one charge event per successful jp_invoice_check call.
The other tools are free. Outside Apify nothing is charged and nothing is sent anywhere.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from . import policy as _policy
from . import server as _server

MAX_BODY = 1_000_000
MCP_PATH = "/mcp"
CHARGED_TOOLS = ("jp_invoice_check",)


def _charged(msg, resp):
    """True when this message was a successful call of a charged tool."""
    if not isinstance(msg, dict) or msg.get("method") != "tools/call":
        return False
    name = (msg.get("params") or {}).get("name")
    result = (resp or {}).get("result") or {}
    return name in CHARGED_TOOLS and "error" not in (resp or {}) and result.get("isError") is False


def process(body, charge=None, request_id=None):
    """Handle one HTTP body. Returns (status, payload or None). `charge(event, request_id)` is called per charged call."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    batch = isinstance(data, list)
    msgs = data if batch else [data]
    if not msgs:
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    out = []
    for m in msgs:
        if not isinstance(m, dict):
            out.append({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
            continue
        resp = _server.handle(m)
        if resp is None:
            continue
        if charge and _charged(m, resp):
            charge("invoice-checked", request_id)
        out.append(resp)
    if not out:
        return 202, None
    return 200, (out if batch else out[0])


def make_handler(charge=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "jp-invoice-check/" + __version__
        sys_version = ""

        def log_message(self, fmt, *args):  # no request logging (bodies may hold invoice data)
            pass

        def _send(self, status, payload=None, ctype="application/json"):
            raw = b"" if payload is None else (
                payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            self.send_response(status)
            if raw:
                self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if raw:
                self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, "ok", "text/plain")
            elif path == MCP_PATH:
                self._send(405, {"error": "Use POST. This server does not open an SSE stream."})
            else:
                self._send(404, {"error": "not found"})

        def do_DELETE(self):
            self._send(405, {"error": "No sessions to close."})

        def do_POST(self):
            if self.path.split("?", 1)[0] != MCP_PATH:
                self._send(404, {"error": "not found"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                self._send(413, {"error": "body too large or bad Content-Length"})
                return
            body = self.rfile.read(n)
            status, payload = process(body, charge, self.headers.get("X-Actor-Request-ID"))
            self._send(status, payload)

    return Handler


def serve(host="127.0.0.1", port=8765, charge=None):
    return ThreadingHTTPServer((host, port), make_handler(charge))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default

    _server.POLICY = _policy.load(opt("--policy"))
    on_apify = bool(os.environ.get("ACTOR_WEB_SERVER_PORT"))
    port = int(opt("--port", os.environ.get("ACTOR_WEB_SERVER_PORT", "8765")))
    host = opt("--host", "0.0.0.0" if on_apify else "127.0.0.1")
    charge = None
    if on_apify:
        from .apify_charge import charge_from_env
        charge = charge_from_env()
    httpd = serve(host, port, charge)
    sys.stderr.write(f"jp-invoice-check {__version__} MCP over HTTP on http://{host}:{port}{MCP_PATH}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
