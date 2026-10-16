"""Tests for cronwrap.runner — execution, exit codes, timeouts, log rotation."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cronwrap.runner import RunResult, _safe_name, run_command


class TestSafeName(unittest.TestCase):
    def test_slugifies(self):
        self.assertEqual(_safe_name("nightly backup!"), "nightly_backup")
        self.assertEqual(_safe_name("a/b\\c"), "a_b_c")
        self.assertEqual(_safe_name("..."), "job")

    def test_keeps_normal(self):
        self.assertEqual(_safe_name("db-dump_01"), "db-dump_01")


class TestRunCommand(unittest.TestCase):
    def test_success_exit_zero(self):
        r = run_command(["true"], name="ok-job")
        self.assertTrue(r.ok)
        self.assertEqual(r.exit_code, 0)
        self.assertFalse(r.timed_out)

    def test_failure_exit_code_passthrough(self):
        r = run_command(["false"], name="fail-job")
        self.assertFalse(r.ok)
        self.assertEqual(r.exit_code, 1)
        self.assertFalse(r.timed_out)

    def test_exit_code_2_passthrough(self):
        r = run_command(
            [sys.executable, "-c", "import sys; sys.exit(2)"], name="exit2"
        )
        self.assertEqual(r.exit_code, 2)
        self.assertFalse(r.ok)

    def test_captures_stdout_and_stderr(self):
        r = run_command(
            [sys.executable, "-c", "print('out-line'); import sys; print('err-line', file=sys.stderr)"],
            name="capture",
        )
        self.assertIn("out-line", r.stdout)
        self.assertIn("err-line", r.stderr)
        self.assertEqual(r.exit_code, 0)

    def test_timeout_kills_process(self):
        r = run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            name="slow",
            timeout=1.0,
        )
        self.assertTrue(r.timed_out)
        self.assertIsNotNone(r.exit_code)
        self.assertLess(r.duration_s, 10)

    def test_log_file_and_rotation(self):
        with tempfile.TemporaryDirectory() as log_dir:
            for _ in range(5):
                run_command(["true"], name="rot-job", log_dir=log_dir, keep_logs=3)
                time.sleep(1.05)  # distinct second-resolution timestamp
            files = [f for f in os.listdir(log_dir) if f.endswith(".log")]
            self.assertEqual(len(files), 3)
            last = run_command(["false"], name="rot-job", log_dir=log_dir, keep_logs=3)
            self.assertIsNotNone(last.log_path)
            self.assertTrue(os.path.isfile(last.log_path))
            with open(last.log_path) as fh:
                content = fh.read()
            self.assertIn("exit: 1", content)
            self.assertIn("--- stdout ---", content)
            self.assertIn("--- stderr ---", content)

    def test_log_dir_created(self):
        with tempfile.TemporaryDirectory() as base:
            log_dir = os.path.join(base, "nested", "logs")
            r = run_command(["true"], name="mkdir", log_dir=log_dir)
            self.assertTrue(os.path.isdir(log_dir))
            self.assertIsNotNone(r.log_path)

    def test_empty_command_raises(self):
        with self.assertRaises(ValueError):
            run_command([], name="empty")

    def test_tail_combines_streams(self):
        r = run_command(
            [sys.executable, "-c", "print('A'); import sys; print('B', file=sys.stderr)"],
            name="tail",
        )
        self.assertIn("A", r.tail)
        self.assertIn("B", r.tail)

    def test_large_output_no_deadlock(self):
        # 200k of output on both streams; threaded drain must keep up.
        r = run_command(
            [sys.executable, "-c",
             "import sys\n"
             "blob = 'x' * 1000\n"
             "for _ in range(100):\n"
             "    sys.stdout.write(blob + '\\n'); sys.stderr.write(blob + '\\n')\n"],
            name="flood",
            timeout=20,
        )
        self.assertEqual(r.exit_code, 0)
        self.assertGreater(len(r.stdout), 90000)

    def test_killed_children_on_timeout(self):
        # Child spawns a grandchild; timeout must kill the whole group.
        r = run_command(
            [sys.executable, "-c",
             "import subprocess, sys, time\n"
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
             "time.sleep(30)\n"],
            name="group-kill",
            timeout=1.0,
        )
        self.assertTrue(r.timed_out)


class TestRunResult(unittest.TestCase):
    def test_ok_property(self):
        r = RunResult(
            name="x", command=["true"], exit_code=0, timed_out=False,
            duration_s=0.1, stdout="", stderr="", log_path=None,
            started_at=None,
        )
        self.assertTrue(r.ok)
        r.timed_out = True
        self.assertFalse(r.ok)


if __name__ == "__main__":
    unittest.main()
