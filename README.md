# cronwrap

Wrap any cron job: run it, capture logs, and get **Telegram** / **Discord** alerts on failure or timeout. Zero dependencies — Python 3.9+ standard library only.

Cron failures are silent by default. You only find out when someone complains. `cronwrap` sits between cron and your command: it runs the command, captures stdout/stderr, rotates log files, and pings you the moment something goes wrong.

## Features

- **Run anything** — arbitrary shell command with arguments; the original exit code is passed through to the caller (cron still sees the real status).
- **Timeout** — `--timeout SECONDS` kills commands that run too long (whole process group, so spawned children die too) and reports exit code `124`.
- **Telegram + Discord alerts** — via `urllib` stdlib only, no third-party packages. Alert includes job name, exit code, duration, and a log tail.
- **Notification policy** — `--notify-on failure|always|never` (default: `failure`).
- **Log rotation** — `--log-dir DIR` writes a timestamped log per run and keeps only the N newest files per job (`--keep-logs`, default 10).
- **Dry run** — `--dry-run` prints what would be executed without running anything.
- **Env-var config** — every notifier setting can come from `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL` (CLI flags win over env vars).

## Installation

Python 3.9+ required. No dependencies to install.

```bash
git clone https://github.com/agenticspring/cronwrap.git
cd cronwrap
pip install .
# or just run it directly:
python3 -m cronwrap --help
```

## Configuration

All notification settings accept either a CLI flag or an environment variable (flag wins).

| Environment variable   | CLI flag                 | Description                                  |
|------------------------|--------------------------|----------------------------------------------|
| `TELEGRAM_BOT_TOKEN`   | `--telegram-bot-token`   | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID`     | `--telegram-chat-id`     | Chat ID to message (from [@userinfobot](https://t.me/userinfobot)) |
| `DISCORD_WEBHOOK_URL`  | `--discord-webhook-url`  | Discord channel webhook URL                  |

Copy `.env.example` to `.env` and fill in real values (`.env` is git-ignored — never commit real tokens). Or export the variables directly:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABCDEF-your-token-here"
export TELEGRAM_CHAT_ID="123456789"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

A channel is only used when **all** of its settings are present. If no channel is configured, cronwrap still runs the command and reports locally — it just has nowhere to send alerts.

## Usage

Basic form — everything after `--` is your command, verbatim:

```bash
cronwrap [options] -- <command> [args...]
```

### Real-world examples

**1. Nightly backup with alerts on failure**

```bash
0 2 * * * /usr/local/bin/cronwrap --name nightly-backup --timeout 3600 \
    --log-dir /var/log/cronwrap -- /usr/local/bin/backup.sh --full
```

**2. Database dump with env-var config**

```bash
export TELEGRAM_BOT_TOKEN="..." TELEGRAM_CHAT_ID="..."
0 3 * * * cronwrap --name db-dump --timeout 1800 --notify-on failure \
    --log-dir /var/log/cronwrap -- pg_dump mydb | gzip > /backups/mydb.sql.gz
```

**3. Health check that alerts every run (success or failure)**

```bash
*/5 * * * * cronwrap --name api-health --notify-on always \
    --discord-webhook-url "$DISCORD_WEBHOOK_URL" -- curl -fsS https://api.example.com/health
```

**4. Preview without executing**

```bash
cronwrap --name db-dump --timeout 600 --dry-run -- pg_dump mydb
# [dry-run] job: db-dump
# [dry-run] command: pg_dump mydb
# [dry-run] timeout: 600.0
# ...
```

### Alert format

When a job fails or times out, you get a message like:

```
cronwrap alert: nightly-backup FAILED (exit 3)
Command: /usr/local/bin/backup.sh --full
Duration: 42.7s
Exit code: 3
Log: /var/log/cronwrap/nightly-backup.20261016-020000.log

Log tail:
```
...last lines of stdout/stderr...
```
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Command succeeded (dry-run also exits 0) |
| `N`  | The command's own non-zero exit code (passed through untouched) |
| `124` | The command was killed for exceeding `--timeout` |

## Limitations

- **Single host** — no distributed scheduling, no web dashboard; cronwrap wraps one command per invocation.
- **Notification failures never fail the job** — if Telegram/Discord is unreachable, the error is printed to stderr but the command's exit code is preserved.
- **No retries** — a failed job is reported once; add retry logic to your own script if needed.
- **Telegram message length** — the log tail is truncated to ~3500 characters to stay under Telegram's message limit.
- **Log rotation is count-based** — rotation keeps the newest N files per job, not a size/age cap.
- **Log filenames use second resolution** — two runs of the same job within one second share a log file.

## Development

```bash
python3 -m unittest discover -s tests -v
```

42 tests across runner (execution, timeouts, log rotation), notify (payloads, policies, mock-HTTP delivery), and CLI (parsing, env fallback, exit codes).

## License

MIT — see [LICENSE](LICENSE).
