"""Tests for cronwrap.notify — payload building, policies, mock HTTP delivery."""
import datetime
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cronwrap.notify import (
    NotifyConfig,
    build_message,
    notify,
    send_discord,
    send_telegram,
    should_notify,
)
from cronwrap.runner import RunResult


def make_result(**kwargs):
    base = dict(
        name="backup",
        command=["/usr/local/bin/backup.sh", "--full"],
        exit_code=3,
        timed_out=False,
        duration_s=12.34,
        stdout="line one\nline two\n",
        stderr="disk read error\n",
        log_path="/var/log/cronwrap/backup.log",
        started_at=datetime.datetime.now(datetime.timezone.utc),
    )
    base.update(kwargs)
    return RunResult(**base)


class _CaptureHandler(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CaptureHandler.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


class MockHTTPMixin:
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        _CaptureHandler.requests.clear()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"


class TestBuildMessage(unittest.TestCase):
    def test_failure_message_fields(self):
        msg = build_message(make_result())
        self.assertIn("backup", msg)
        self.assertIn("exit 3", msg)
        self.assertIn("12.3s", msg)
        self.assertIn("--full", msg)
        self.assertIn("disk read error", msg)

    def test_timeout_message(self):
        msg = build_message(make_result(timed_out=True, exit_code=None))
        self.assertIn("TIMEOUT", msg)

    def test_ok_message(self):
        msg = build_message(make_result(exit_code=0))
        self.assertIn("OK", msg)

    def test_tail_truncation(self):
        msg = build_message(
            make_result(stdout=("y" * 200 + "\n") * 100), max_log_chars=100
        )
        self.assertIn("…", msg)


class TestShouldNotify(unittest.TestCase):
    def test_never(self):
        self.assertFalse(should_notify("never", make_result()))

    def test_always(self):
        self.assertTrue(should_notify("always", make_result(exit_code=0)))
        self.assertTrue(should_notify("always", make_result()))

    def test_failure(self):
        self.assertTrue(should_notify("failure", make_result()))
        self.assertFalse(should_notify("failure", make_result(exit_code=0)))
        self.assertTrue(
            should_notify("failure", make_result(timed_out=True, exit_code=None))
        )


class TestDelivery(MockHTTPMixin, unittest.TestCase):
    def test_send_telegram_hits_api_path(self):
        # Verify the Telegram API URL shape and JSON body (spy the HTTP layer).
        import cronwrap.notify as notify_mod

        orig = notify_mod._post_json
        captured = {}

        def spy(url, payload, timeout=15.0):
            captured["url"] = url
            captured["payload"] = payload
            return 200

        notify_mod._post_json = spy
        try:
            ok = send_telegram("DUMMYTOKEN", "12345", "hello")
        finally:
            notify_mod._post_json = orig
        self.assertTrue(ok)
        self.assertIn("/botDUMMYTOKEN/sendMessage", captured["url"])
        self.assertEqual(captured["payload"]["chat_id"], "12345")
        self.assertEqual(captured["payload"]["text"], "hello")

    def test_send_discord_real_mock_server(self):
        ok = send_discord(self.base_url + "/webhooks/hook123", "alert text")
        self.assertTrue(ok)
        self.assertEqual(len(_CaptureHandler.requests), 1)
        req = _CaptureHandler.requests[0]
        self.assertEqual(req["path"], "/webhooks/hook123")
        self.assertEqual(json.loads(req["body"])["content"], "alert text")

    def test_notify_reports_attempts(self):
        cfg = NotifyConfig(
            telegram_bot_token="DUMMY",
            telegram_chat_id="1",
            discord_webhook_url=self.base_url + "/hook",
        )
        # Mock telegram path only; discord goes to the real mock server.
        import cronwrap.notify as notify_mod

        orig = notify_mod._post_json

        def spy(url, payload, timeout=15.0):
            if "api.telegram.org" in url:
                return 200
            return orig(url, payload, timeout)

        notify_mod._post_json = spy
        try:
            report = notify(make_result(), cfg, notify_on="failure")
        finally:
            notify_mod._post_json = orig
        self.assertIn("telegram", report.succeeded)
        self.assertIn("discord", report.succeeded)
        self.assertEqual(report.failed, [])

    def test_notify_skipped_on_success_policy(self):
        cfg = NotifyConfig(discord_webhook_url=self.base_url + "/hook")
        report = notify(make_result(exit_code=0), cfg, notify_on="failure")
        self.assertEqual(report.attempted, [])
        self.assertEqual(len(_CaptureHandler.requests), 0)


class TestEnabledChannels(unittest.TestCase):
    def test_none(self):
        self.assertEqual(NotifyConfig().enabled_channels(), [])

    def test_partial_telegram(self):
        cfg = NotifyConfig(telegram_bot_token="DUMMY")
        self.assertEqual(cfg.enabled_channels(), [])

    def test_all(self):
        cfg = NotifyConfig(
            telegram_bot_token="DUMMY",
            telegram_chat_id="1",
            discord_webhook_url="https://discord.example/hook",
        )
        self.assertEqual(cfg.enabled_channels(), ["telegram", "discord"])


if __name__ == "__main__":
    unittest.main()
