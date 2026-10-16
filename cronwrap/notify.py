"""Notification layer for cronwrap: Telegram bot + Discord webhook.

Both transports use only the Python standard library (``urllib.request``).
Failures to deliver a notification are logged to stderr but never change the
wrapped command's exit code — alerting must never mask the real result.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .runner import RunResult


@dataclass
class NotifyConfig:
    """Notification endpoints. Any field may be None (channel disabled)."""

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None

    def enabled_channels(self) -> list[str]:
        channels: list[str] = []
        if self.telegram_bot_token and self.telegram_chat_id:
            channels.append("telegram")
        if self.discord_webhook_url:
            channels.append("discord")
        return channels


def _post_json(url: str, payload: dict, timeout: float = 15.0) -> int:
    """POST a JSON body; return the HTTP status code."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "cronwrap/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def build_message(result: RunResult, max_log_chars: int = 3500) -> str:
    """Human-readable alert body shared by both channels."""
    status = "TIMEOUT" if result.timed_out else f"FAILED (exit {result.exit_code})"
    if result.ok:
        status = "OK"
    lines = [
        f"cronwrap alert: {result.name} {status}",
        f"Command: {' '.join(result.command)}",
        f"Duration: {result.duration_s:.1f}s",
    ]
    if result.timed_out:
        lines.append("Reason: exceeded --timeout, process killed")
    elif result.exit_code is not None:
        lines.append(f"Exit code: {result.exit_code}")
    if result.log_path:
        lines.append(f"Log: {result.log_path}")
    tail = result.tail
    if len(tail) > max_log_chars:
        tail = tail[-max_log_chars:]
        tail = "…\n" + tail
    lines.append("")
    lines.append("Log tail:")
    lines.append("```")
    lines.append(tail)
    lines.append("```")
    return "\n".join(lines)


def send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    """Deliver *text* via the Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": None,
        "disable_web_page_preview": True,
    }
    try:
        status = _post_json(url, payload)
        return 200 <= status < 300
    except Exception as exc:  # noqa: BLE001 - alerting must not crash the wrapper
        print(f"cronwrap: telegram notification failed: {exc}", file=sys.stderr)
        return False


def send_discord(webhook_url: str, text: str) -> bool:
    """Deliver *text* via a Discord webhook. Returns True on success."""
    payload = {"content": text}
    try:
        status = _post_json(webhook_url, payload)
        return 200 <= status < 300
    except Exception as exc:  # noqa: BLE001 - alerting must not crash the wrapper
        print(f"cronwrap: discord notification failed: {exc}", file=sys.stderr)
        return False


@dataclass
class NotifyReport:
    attempted: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def should_notify(notify_on: str, result: RunResult) -> bool:
    """Decide whether an alert is due, per ``--notify-on`` policy."""
    if notify_on == "never":
        return False
    if notify_on == "always":
        return True
    # "failure": timeout or non-zero exit
    return not result.ok


def notify(result: RunResult, config: NotifyConfig, notify_on: str = "failure") -> NotifyReport:
    """Send alerts per policy. Returns a report of what happened."""
    report = NotifyReport()
    if not should_notify(notify_on, result):
        return report
    message = build_message(result)
    if config.telegram_bot_token and config.telegram_chat_id:
        report.attempted.append("telegram")
        if send_telegram(config.telegram_bot_token, config.telegram_chat_id, message):
            report.succeeded.append("telegram")
        else:
            report.failed.append("telegram")
    if config.discord_webhook_url:
        report.attempted.append("discord")
        if send_discord(config.discord_webhook_url, message):
            report.succeeded.append("discord")
        else:
            report.failed.append("discord")
    return report
