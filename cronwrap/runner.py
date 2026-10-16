"""Process execution engine for cronwrap.

Runs an arbitrary shell command, captures stdout/stderr, enforces a timeout,
and persists a rotated log file per job name. Returns a :class:`RunResult`
that the CLI hands to the notification layer.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass


@dataclass
class RunResult:
    """Outcome of one wrapped command execution."""

    name: str
    command: list[str]
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout: str
    stderr: str
    log_path: str | None
    started_at: datetime.datetime

    @property
    def ok(self) -> bool:
        """True only when the command exited 0 on its own."""
        return not self.timed_out and self.exit_code == 0

    @property
    def tail(self) -> str:
        """Combined last lines of stdout+stderr for alert payloads."""
        combined = (self.stdout + "\n" + self.stderr).strip()
        if not combined:
            return "(no output)"
        lines = combined.splitlines()
        return "\n".join(lines[-30:])

    def summary(self) -> str:
        status = "timed out" if self.timed_out else f"exit code {self.exit_code}"
        return (
            f"cronwrap job '{self.name}': {status} "
            f"after {self.duration_s:.1f}s — {' '.join(self.command)}"
        )


def _safe_name(name: str) -> str:
    """Slugify a job name for safe log filenames."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return slug.strip("._") or "job"


def _prune_logs(log_dir: str, slug: str, keep: int) -> None:
    """Keep at most ``keep`` log files for this job, deleting oldest first."""
    files = sorted(
        f for f in os.listdir(log_dir)
        if f.startswith(f"{slug}.") and f.endswith(".log")
    )
    while len(files) > keep:
        oldest = files.pop(0)
        try:
            os.remove(os.path.join(log_dir, oldest))
        except OSError:
            pass


def run_command(
    command: list[str],
    *,
    name: str,
    timeout: float | None = None,
    log_dir: str | None = None,
    keep_logs: int = 10,
    stdin_text: str | None = None,
) -> RunResult:
    """Run *command* with an optional *timeout* (seconds).

    stdout and stderr are captured in parallel via threads so a chatty child
    can never deadlock the parent. The child is started in its own process
    group; on timeout the whole group is terminated (SIGKILL fallback).

    When *log_dir* is given, a timestamped log file is written and only
    *keep_logs* newest files per job are retained.

    The returned :class:`RunResult` carries the real exit code; the caller
    decides what to do with it (cronwrap's CLI re-exits with the same code,
    or a sentinel when the job timed out).
    """
    if not command:
        raise ValueError("command must not be empty")
    started = datetime.datetime.now(datetime.timezone.utc)
    slug = _safe_name(name)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def _drain(stream, sink: list[bytes]) -> None:
        try:
            for chunk in iter(lambda: stream.read(65536), b""):
                sink.append(chunk)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # own process group -> kill kills children too
        text=False,
    )

    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True),
    ]
    for t in readers:
        t.start()

    timed_out = False
    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = None
        # Kill the whole process group so spawned children die too.
        try:
            os.killpg(proc.pid, 15)  # SIGTERM
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 9)  # SIGKILL
            except (ProcessLookupError, PermissionError, OSError):
                pass
            proc.wait()
        exit_code = proc.returncode  # negative signal number after kill

    for t in readers:
        t.join(timeout=10)

    duration = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    stdout = b"".join(stdout_chunks).decode("utf-8", "replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", "replace")

    log_path: str | None = None
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as exc:
            # A broken log dir must never mask the command's own result.
            print(
                f"cronwrap: warning: cannot use log dir {log_dir}: {exc} "
                "— continuing without a log file",
                file=sys.stderr,
            )
            log_dir = None
    if log_dir:
        stamp = started.strftime("%Y%m%d-%H%M%S")
        log_path = os.path.join(log_dir, f"{slug}.{stamp}.log")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(f"# cronwrap job: {name}\n")
            fh.write(f"# command: {' '.join(command)}\n")
            fh.write(f"# started: {started.isoformat()}\n")
            fh.write(f"# exit: {exit_code}  timeout: {timed_out}\n")
            fh.write(f"# duration: {duration:.2f}s\n")
            fh.write("=" * 60 + "\n--- stdout ---\n")
            fh.write(stdout)
            fh.write("\n--- stderr ---\n")
            fh.write(stderr)
        _prune_logs(log_dir, slug, keep_logs)

    return RunResult(
        name=name,
        command=list(command),
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration,
        stdout=stdout,
        stderr=stderr,
        log_path=log_path,
        started_at=started,
    )


def main_demo(argv: list[str] | None = None) -> int:  # pragma: no cover
    """Tiny smoke helper: python -m cronwrap.runner <cmd>..."""
    if argv is None:
        argv = sys.argv[1:]
    result = run_command(argv or ["echo", "hello"], name="demo")
    print(result.summary())
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main_demo())
