import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jpinvoice import apify_charge, http_server  # noqa: E402

OK = json.load(open(os.path.join(ROOT, "examples", "ok.json"), encoding="utf-8"))


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.charges = []
        cls.httpd = http_server.serve("127.0.0.1", 0, lambda ev, rid: cls.charges.append((ev, rid)))
        cls.url = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.charges.clear()

    def post(self, payload, path="/mcp", raw=None, headers=None):
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read()
                return r.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, (json.loads(body) if body else None)

    def call(self, name, args, mid=1):
        return self.post({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": args}})

    def test_readiness(self):
        with urllib.request.urlopen(self.url + "/", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read(), b"ok")

    def test_initialize_and_list(self):
        s, r = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(s, 200)
        self.assertEqual(r["result"]["serverInfo"]["name"], "jp-invoice-check")
        s, r = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertIn("jp_invoice_check", [t["name"] for t in r["result"]["tools"]])

    def test_check_is_charged_once(self):
        s, r = self.call("jp_invoice_check", {"invoice": OK}, mid=3)
        self.assertEqual(s, 200)
        self.assertTrue(r["result"]["structuredContent"]["ok"])
        self.assertEqual(self.charges, [("invoice-checked", None)])

    def test_request_id_passed(self):
        self.post({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "jp_invoice_check", "arguments": {"invoice": OK}}},
                  headers={"X-Actor-Request-ID": "req-1"})
        self.assertEqual(self.charges, [("invoice-checked", "req-1")])

    def test_free_tools_and_errors_not_charged(self):
        self.call("jp_invoice_tax", {"amount": 5997, "rate": 10})
        self.call("jp_invoice_registration_number", {"number": "T1234567890123"})
        s, r = self.call("jp_invoice_check", {"invoice": "not an object"})
        self.assertTrue(r["result"]["isError"])
        self.call("no_such_tool", {})
        self.assertEqual(self.charges, [])

    def test_notification_202(self):
        s, r = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((s, r), (202, None))

    def test_batch(self):
        s, r = self.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                          {"jsonrpc": "2.0", "method": "notifications/initialized"}])
        self.assertEqual(s, 200)
        self.assertEqual(len(r), 1)

    def test_bad_json_and_paths(self):
        s, r = self.post(None, raw=b"{not json")
        self.assertEqual((s, r["error"]["code"]), (400, -32700))
        s, _ = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/other")
        self.assertEqual(s, 404)
        try:
            urllib.request.urlopen(self.url + "/mcp", timeout=5)
            self.fail("GET /mcp should be 405")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)

    def test_too_large(self):
        s, _ = self.post(None, raw=b" " * (http_server.MAX_BODY + 1))
        self.assertEqual(s, 413)


class TestCharge(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("ACTOR_RUN_ID", "APIFY_TOKEN")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_off_outside_apify(self):
        os.environ.pop("ACTOR_RUN_ID", None)
        os.environ.pop("APIFY_TOKEN", None)
        self.assertIsNone(apify_charge.charge_from_env())

    def test_request_shape(self):
        os.environ["ACTOR_RUN_ID"] = "run123"
        os.environ["APIFY_TOKEN"] = "dummy-test-value"
        sent = []
        charge = apify_charge.charge_from_env(opener=sent.append)
        self.assertTrue(charge("invoice-checked", "req-9"))
        req = sent[0]
        self.assertEqual(req.full_url, "https://api.apify.com/v2/actor-runs/run123/charge")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"eventName": "invoice-checked", "count": 1, "requestId": "req-9"})
        self.assertTrue(req.get_header("Idempotency-key"))

    def test_failure_does_not_raise(self):
        os.environ["ACTOR_RUN_ID"] = "run123"
        os.environ["APIFY_TOKEN"] = "dummy-test-value"

        def boom(req):
            raise OSError("network down")
        self.assertFalse(apify_charge.charge_from_env(opener=boom)("invoice-checked"))


if __name__ == "__main__":
    unittest.main()
