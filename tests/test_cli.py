"""Tests for cronwrap.cli — arg parsing, env fallback, dry-run, exit codes."""
import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cronwrap.cli import TIMEOUT_EXIT_CODE, build_parser, main


class _Handler(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _Handler.bodies.append(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


class TestParser(unittest.TestCase):
    def test_dashdash_separator(self):
        args = build_parser().parse_args(["--name", "x", "--", "echo", "hi"])
        self.assertEqual(args.command, ["--", "echo", "hi"])

    def test_defaults(self):
        args = build_parser().parse_args(["--", "true"])
        self.assertEqual(args.notify_on, "failure")
        self.assertEqual(args.name, "job")
        self.assertIsNone(args.timeout)

    def test_env_fallback(self):
        with mock.patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "ENVTOKEN", "DISCORD_WEBHOOK_URL": "https://env.example/hook"},
            clear=False,
        ):
            args = build_parser().parse_args(["--", "true"])
        self.assertEqual(args.telegram_bot_token, "ENVTOKEN")
        self.assertEqual(args.discord_webhook_url, "https://env.example/hook")

    def test_flag_beats_env(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "ENVTOKEN"}):
            args = build_parser().parse_args(
                ["--telegram-bot-token", "FLAGTOKEN", "--", "true"]
            )
        self.assertEqual(args.telegram_bot_token, "FLAGTOKEN")

    def test_bad_notify_on_rejected(self):
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                build_parser().parse_args(["--notify-on", "sometimes", "--", "true"])


class TestMain(unittest.TestCase):
    def run_main(self, argv):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = main(argv)
        return code, buf_out.getvalue(), buf_err.getvalue()

    def test_success_exit_zero(self):
        code, _, err = self.run_main(["--name", "ok", "--", "true"])
        self.assertEqual(code, 0)
        self.assertIn("exit code 0", err)

    def test_failure_exit_passthrough(self):
        code, _, _ = self.run_main(["--name", "bad", "--", "false"])
        self.assertEqual(code, 1)

    def test_exit_2_passthrough(self):
        code, _, _ = self.run_main(
            ["--", sys.executable, "-c", "import sys; sys.exit(2)"]
        )
        self.assertEqual(code, 2)

    def test_timeout_exit_124(self):
        code, _, err = self.run_main(
            ["--timeout", "1", "--", sys.executable, "-c", "import time; time.sleep(30)"]
        )
        self.assertEqual(code, TIMEOUT_EXIT_CODE)
        self.assertIn("timed out", err)

    def test_dry_run_executes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "should-not-exist")
            code, out, _ = self.run_main(
                ["--name", "dry", "--timeout", "5", "--dry-run", "--", "touch", marker]
            )
            self.assertEqual(code, 0)
            self.assertIn("[dry-run]", out)
            self.assertIn("touch " + marker, out)
            self.assertFalse(os.path.exists(marker))

    def test_missing_command_errors(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main(["--name", "x"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_discord_notification_on_failure(self):
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _Handler.bodies.clear()
            code, _, err = self.run_main(
                [
                    "--name",
                    "alert-job",
                    "--discord-webhook-url",
                    f"http://127.0.0.1:{port}/hook",
                    "--",
                    "false",
                ]
            )
            self.assertEqual(code, 1)  # exit code still from the command
            self.assertEqual(len(_Handler.bodies), 1)
            self.assertIn(b"alert-job", _Handler.bodies[0])
            self.assertIn(b"exit 1", _Handler.bodies[0])
            self.assertIn(b"notifications sent=[discord]", err.encode())
        finally:
            server.shutdown()
            thread.join()

    def test_no_notification_on_success_default(self):
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _Handler.bodies.clear()
            code, _, _ = self.run_main(
                [
                    "--discord-webhook-url",
                    f"http://127.0.0.1:{port}/hook",
                    "--",
                    "true",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(_Handler.bodies), 0)
        finally:
            server.shutdown()
            thread.join()

    def test_always_notify_on_success(self):
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _Handler.bodies.clear()
            code, _, _ = self.run_main(
                [
                    "--notify-on",
                    "always",
                    "--discord-webhook-url",
                    f"http://127.0.0.1:{port}/hook",
                    "--",
                    "true",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(_Handler.bodies), 1)
            self.assertIn(b"OK", _Handler.bodies[0])
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    unittest.main()
