"""MCP 2026-07-28 (stateless) and legacy initialize both work on the same server."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jpinvoice.server import handle  # noqa: E402

K = "io.modelcontextprotocol/"
META = {K + "protocolVersion": "2026-07-28", K + "clientCapabilities": {}, K + "clientInfo": {"name": "t", "version": "1"}}


def req(method, params=None, mid=1):
    return handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


class Modern(unittest.TestCase):
    def test_discover(self):
        r = req("server/discover", {"_meta": META})["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertEqual(r["supportedVersions"], ["2026-07-28"])
        self.assertIn("tools", r["capabilities"])
        self.assertTrue(r["_meta"][K + "serverInfo"]["name"])
        self.assertIn(r["cacheScope"], ("public", "private"))
        self.assertIsInstance(r["ttlMs"], int)

    def test_tools_list_without_initialize(self):
        r = req("tools/list", {"_meta": META})["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertTrue(r["tools"])
        self.assertEqual(r["cacheScope"], "public")
        self.assertIsInstance(r["ttlMs"], int)
        self.assertIn(K + "serverInfo", r["_meta"])

    def test_tools_call_without_initialize(self):
        tools = req("tools/list", {"_meta": META})["result"]["tools"]
        r = req("tools/call", {"_meta": META, "name": tools[0]["name"], "arguments": {}})["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertIn("isError", r)

    def test_unknown_tool_is_protocol_error(self):
        r = req("tools/call", {"_meta": META, "name": "nope", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_unsupported_version(self):
        r = req("server/discover", {"_meta": dict(META, **{K + "protocolVersion": "1900-01-01"})})
        self.assertEqual(r["error"]["code"], -32022)
        self.assertIn("2026-07-28", r["error"]["data"]["supported"])
        self.assertEqual(r["error"]["data"]["requested"], "1900-01-01")

    def test_missing_fields_rejected(self):
        self.assertEqual(req("server/discover", {})["error"]["code"], -32602)
        meta = {K + "protocolVersion": "2026-07-28"}
        self.assertEqual(req("tools/list", {"_meta": meta})["error"]["code"], -32602)

    def test_removed_methods(self):
        self.assertEqual(req("ping", {"_meta": META})["error"]["code"], -32601)

    def test_notification_gets_no_reply(self):
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "server/discover", "params": {"_meta": META}}))


class Legacy(unittest.TestCase):
    def test_initialize_still_works(self):
        r = req("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertNotIn("resultType", req("tools/list")["result"])


if __name__ == "__main__":
    unittest.main()
