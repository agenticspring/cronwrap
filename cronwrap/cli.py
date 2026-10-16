"""Command-line interface for cronwrap.

Usage: cronwrap [options] -- <command> [args...]

Everything before the command is cronwrap's own flags; everything after the
command is executed verbatim. Configuration can come from CLI flags or from
environment variables (flag wins when both are set).
"""

from __future__ import annotations

import argparse
import os
import sys

from .notify import NotifyConfig, NotifyReport, notify
from .runner import run_command

#: Exit code used when the job is killed due to --timeout.
TIMEOUT_EXIT_CODE = 124


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cronwrap",
        description=(
            "Wrap any cron job: run a command, capture its output, rotate logs, "
            "and send Telegram/Discord alerts on failure or timeout."
        ),
    )
    parser.add_argument(
        "--name",
        default="job",
        help="Job name used in alerts and log filenames (default: 'job').",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Kill the command if it runs longer than this many seconds.",
    )
    parser.add_argument(
        "--notify-on",
        choices=("failure", "always", "never"),
        default="failure",
        help="When to send alerts (default: failure).",
    )
    parser.add_argument(
        "--telegram-bot-token",
        default=_env("TELEGRAM_BOT_TOKEN"),
        help="Telegram bot token (or TELEGRAM_BOT_TOKEN env).",
    )
    parser.add_argument(
        "--telegram-chat-id",
        default=_env("TELEGRAM_CHAT_ID"),
        help="Telegram chat ID to message (or TELEGRAM_CHAT_ID env).",
    )
    parser.add_argument(
        "--discord-webhook-url",
        default=_env("DISCORD_WEBHOOK_URL"),
        help="Discord webhook URL (or DISCORD_WEBHOOK_URL env).",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Directory to write per-run log files to.",
    )
    parser.add_argument(
        "--keep-logs",
        type=int,
        default=10,
        help="Max log files to keep per job (default: 10).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run and exit 0 without executing anything.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="The command to run, e.g. -- /usr/local/bin/backup.sh --full",
    )
    return parser


def _strip_leading_dashdash(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]
    return command


def resolve_config(args: argparse.Namespace) -> NotifyConfig:
    return NotifyConfig(
        telegram_bot_token=args.telegram_bot_token,
        telegram_chat_id=args.telegram_chat_id,
        discord_webhook_url=args.discord_webhook_url,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = _strip_leading_dashdash(list(args.command))

    if args.dry_run:
        name = args.name
        print(f"[dry-run] job: {name}")
        print(f"[dry-run] command: {' '.join(command) if command else '(no command)'}")
        print(f"[dry-run] timeout: {args.timeout or 'none'}")
        print(f"[dry-run] notify-on: {args.notify_on}")
        cfg = resolve_config(args)
        channels = cfg.enabled_channels()
        print(f"[dry-run] channels: {', '.join(channels) if channels else 'none'}")
        print(f"[dry-run] log-dir: {args.log_dir or 'none'} (keep {args.keep_logs})")
        return 0

    if not command:
        parser.error("no command given (usage: cronwrap [options] -- <command> ...)")

    result = run_command(
        command,
        name=args.name,
        timeout=args.timeout,
        log_dir=args.log_dir,
        keep_logs=args.keep_logs,
    )

    report: NotifyReport | None = None
    if result.timed_out or not result.ok or args.notify_on == "always":
        report = notify(result, resolve_config(args), notify_on=args.notify_on)

    print(result.summary(), file=sys.stderr)
    if report is not None and report.attempted:
        ok = ",".join(report.succeeded) if report.succeeded else "none"
        fail = ",".join(report.failed) if report.failed else "none"
        print(f"cronwrap: notifications sent=[{ok}] failed=[{fail}]", file=sys.stderr)

    if result.timed_out:
        return TIMEOUT_EXIT_CODE
    return result.exit_code if result.exit_code is not None else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
