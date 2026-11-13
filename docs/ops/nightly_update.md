# Nightly market-data update (local workstation)

Two scripts refresh the live database and write the day's screen. They do the same thing:

| OS | Script |
|---|---|
| Linux, macOS | [`scripts/nightly_update.sh`](../../scripts/nightly_update.sh) (bash) |
| Windows | [`scripts/nightly_update.ps1`](../../scripts/nightly_update.ps1) (Windows PowerShell 5.1 or PowerShell 7) |

Each run:

1. `cd`s to the repo root (the parent of `scripts/`), so the checkout can live anywhere.
2. Takes a lock (`logs/.nightly.lock`). If another run holds it, the script logs `SKIP` and exits 0.
3. Runs these steps through `uv run`:
   ```
   valuation ingest --universe universe/tsx_composite.csv
   valuation ingest --universe universe/sp500.csv
   valuation value --out reports
   ```
   Ingest uses the default incremental policy (README, "Refresh policy"): prices, volume and benchmarks every run,
   fundamentals and profiles only when due. One `value` run covers every ingested ticker (both universes) and writes
   `reports/<YYYY-MM-DD>/`. Two `value` runs would write the same files, and the second run would overwrite the first.
4. Retries a failed step once after `TRADE2_RETRY_DELAY` seconds (default 300), because Yahoo throttles (HTTP 429)
   and the DuckDB file may be briefly locked. If an ingest still fails, the script still ingests the other market, but
   it skips `value`, so you get no report for that day rather than a half-stale one.
5. Logs to `logs/nightly_<YYYY-MM-DD>.log` (gitignored) and deletes logs older than 30 days.
6. Exits 0 on success, 1 if a step failed, and 127 if `uv` is missing. On failure it runs `TRADE2_NOTIFY_CMD`, if
   you set one.

The scripts run `ingest` and `value` only. **They never run a backtest command** and never touch the frozen models or
the holdout (a unit test checks this).

## Options

| Setting | Default | Meaning |
|---|---|---|
| `--dry-run` / `-DryRun` | | Print the steps, the uv path and the report directory, then exit. It takes no lock, writes no log and fetches nothing. It exits 127 if uv is not found, so it doubles as a setup check. |
| `--deep` / `-Deep` | off | Add `--refresh-history` to both ingests (a full 2-year chart refetch per ticker). |
| `TRADE2_DEEP_REFRESH_DAY` | unset | ISO weekday 1–7 (1 = Mon, 6 = Sat) on which the deep refresh happens automatically. The scheduler must actually run on that day (see "Weekly deep refresh"). |
| `TRADE2_RETRY_DELAY` | `300` | Seconds before the single retry. |
| `TRADE2_LOG_KEEP_DAYS` | `30` | Log retention in days. |
| `TRADE2_UV` | first `uv` on PATH | Full path to the uv binary, if the scheduler can't find it. |
| `TRADE2_NOTIFY_CMD` | unset | A command run on failure. In bash it runs through `bash -c` with the last 40 log lines on stdin. In PowerShell it runs through `Invoke-Expression`. Both get `TRADE2_STATUS`, `TRADE2_FAILED_STEPS` and `TRADE2_LOG` (the log path) in the environment. |
| `VALUATION_SEC_USER_AGENT` | unset | Passed through to SEC EDGAR. SEC's fair-access policy asks for `"<app> <contact email>"`. Set it in your scheduler or profile. The scripts never set it, and the log notes when it is missing. |

Notification examples (use your own topic or address; nothing is hard-coded):

```bash
# ntfy (Linux/macOS)
export TRADE2_NOTIFY_CMD='curl -fsS -H "Title: trade2 nightly failed" -d "$TRADE2_FAILED_STEPS" https://ntfy.sh/<your-topic>'
# local mail
export TRADE2_NOTIFY_CMD='mail -s "trade2 nightly failed: $TRADE2_FAILED_STEPS" <you>'
# macOS banner
export TRADE2_NOTIFY_CMD="osascript -e 'display notification \"nightly update failed\" with title \"trade2\"'"
```
```powershell
# Windows: ntfy via Invoke-RestMethod
[Environment]::SetEnvironmentVariable('TRADE2_NOTIFY_CMD',
  'Invoke-RestMethod -Method Post -Uri https://ntfy.sh/<your-topic> -Body "trade2 failed: $env:TRADE2_FAILED_STEPS"', 'User')
```

## When to run

Run after the market close plus Yahoo's settlement. **We recommend 19:30 America/Toronto, Monday to Friday.**

- TSX and NYSE/Nasdaq close at 16:00 Eastern. Yahoo's end-of-day bars (final close, volume, adjusted close) usually
  settle within an hour or two. EDGAR disseminates the day's filings through the evening (filings accepted after
  17:30 Eastern are dated the next business day). 19:30 picks up a complete trading day.
- FRED `DGS10` publishes with a one-day lag, so the US 10-year rate in a run is always the previous business day's,
  whenever you run.
- Weekends add nothing, and a market holiday is harmless (prices don't change and fundamentals are re-checked).
- A full run for about 720 tickers takes minutes to tens of minutes, depending on how many fundamentals are due. The
  first run on an empty database is the slow one.

**Time zones and DST.** cron, launchd and Task Scheduler all fire on the machine's *local* clock:

- If the machine is set to Eastern time, write 19:30. DST follows automatically, since the job fires at 19:30 local
  both in EST and in EDT.
- If the machine is in another zone, convert. 19:30 Toronto is 16:30 in Vancouver and 01:30 the next morning in
  Amsterdam. Note that in the next-morning case `reports/<date>/` and `--as-of` use the *local* date, which is the day
  after the trading day. Europe and North America switch DST on different dates, so for a few weeks a year the offset
  is off by an hour. Pick a time that is still after the close when that happens.
- If the machine runs on UTC, 00:30 UTC (19:30 EST / 20:30 EDT, the next UTC day) works all year.
- A systemd timer can name the zone explicitly (`OnCalendar=Mon..Fri 19:30 America/Toronto`), which avoids all of the
  above. The provided timer does this. cronie (Fedora/RHEL/Arch) also supports `CRON_TZ=America/Toronto`, but
  Debian/Ubuntu cron does not.

## First: one manual run

Run the script once by hand on each machine. This creates `logs/`, checks that uv and the universe files are in place,
and does the slow first ingest while you watch:

```bash
scripts/nightly_update.sh --dry-run      # plan + uv check
scripts/nightly_update.sh                # real run; tail -f logs/nightly_$(date +%F).log in another shell
```
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\nightly_update.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\nightly_update.ps1
```

## Linux

### Option A: crontab (simplest)

`crontab -e`, then add a line like this (replace the path; the script logs itself, so cron's own output is discarded):

```cron
30 19 * * 1-5 /home/you/src/trade2/scripts/nightly_update.sh >/dev/null 2>&1
```

Cron's environment is minimal: `PATH=/usr/bin:/bin`, and your shell profile is not read. The script appends
`~/.local/bin` and `~/.cargo/bin` (uv's installer default locations), `/opt/homebrew/bin` and `/usr/local/bin` to PATH
itself. If uv is elsewhere, add `PATH=...` or `TRADE2_UV=/full/path/to/uv` as a line above the job. Put
`VALUATION_SEC_USER_AGENT=...` and `TRADE2_NOTIFY_CMD=...` there too. Cron doesn't run while the machine is off or
asleep, and it doesn't catch up afterwards.

### Option B: systemd user timer (zone-aware, catches up)

Templates: [`scripts/systemd/trade2-nightly.service`](../../scripts/systemd/trade2-nightly.service) and
[`trade2-nightly.timer`](../../scripts/systemd/trade2-nightly.timer).

```bash
mkdir -p ~/.config/systemd/user
cp scripts/systemd/trade2-nightly.{service,timer} ~/.config/systemd/user/
sed -i "s#/path/to/trade2#$PWD#" ~/.config/systemd/user/trade2-nightly.service
systemctl --user daemon-reload
systemctl --user enable --now trade2-nightly.timer
loginctl enable-linger "$USER"     # without this, user timers only run while you are logged in
systemctl --user list-timers trade2-nightly.timer   # shows the next fire time
```

The timer fires at `Mon..Fri 19:30 America/Toronto` whatever the machine's zone (this needs systemd 235 or later;
check with `systemctl --version`). With `Persistent=true`, a fire missed while the machine was off or asleep runs once
at the next boot or resume. To add environment variables, uncomment the `Environment=` lines in the service, or run
`systemctl --user edit trade2-nightly.service`.

## macOS

Template: [`scripts/launchd/ca.trade2.nightly.plist`](../../scripts/launchd/ca.trade2.nightly.plist).

```bash
sed "s#/path/to/trade2#$PWD#g" scripts/launchd/ca.trade2.nightly.plist > ~/Library/LaunchAgents/ca.trade2.nightly.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ca.trade2.nightly.plist
launchctl print gui/$(id -u)/ca.trade2.nightly | head -20      # loaded? last exit code?
```

- `StartCalendarInterval` uses the Mac's **local** time (Weekday 1–5, 19:30). If the Mac isn't set to Eastern time,
  convert as described in "When to run".
- **The job runs only while the Mac is awake.** If it is asleep at 19:30, launchd runs the job once when it wakes. If
  it is shut down, the run is skipped. To wake it, use `sudo pmset repeat wakeorpoweron MTWRF 19:25:00` (check with
  `pmset -g sched`). A laptop with the lid closed on battery may still not wake.
- **Privacy protection (TCC):** if the checkout is under `~/Documents`, `~/Desktop`, `~/Downloads` or iCloud Drive,
  background jobs get "Operation not permitted". Move the repo (for example to `~/src/trade2`; recommended), or give
  `/bin/bash` Full Disk Access in System Settings › Privacy & Security.
- launchd's PATH is `/usr/bin:/bin:/usr/sbin:/sbin`. The plist sets a wider PATH, and the script also checks
  `~/.local/bin` and `~/.cargo/bin`. Put `VALUATION_SEC_USER_AGENT` and `TRADE2_NOTIFY_CMD` in the plist's
  `EnvironmentVariables`.
- Do the manual first run before loading the plist. launchd writes its stdout and stderr to `logs/launchd.*.log`, and
  it can't create the `logs/` directory itself.
- To reload after editing the plist: `launchctl bootout gui/$(id -u)/ca.trade2.nightly`, then `bootstrap` again.

## Windows

Install uv (`winget install astral-sh.uv` or the official installer) and run `uv sync` in the repo once.

**PowerShell (recommended), from an elevated prompt in the repo root:**

```powershell
$repo   = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$repo\scripts\nightly_update.ps1`"" `
  -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 19:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U   # run whether logged on or not, no stored password
Register-ScheduledTask -TaskName 'trade2 nightly update' -Action $action -Trigger $trigger -Settings $settings -Principal $principal
```

**Or `schtasks` (one line; prompts for your password, which is how "run whether user is logged on or not" is
stored):**

```bat
schtasks /Create /TN "trade2 nightly update" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 19:30 /RU "%USERDOMAIN%\%USERNAME%" /RP * /TR "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"C:\src\trade2\scripts\nightly_update.ps1\""
```

Notes:

- **Run whether the user is logged on or not:** `-LogonType S4U` does this without storing a password. It has no
  access to network *shares*, but internet access works, which is all the script needs. With `schtasks /RU ... /RP *`
  the password is stored instead. "Run only when user is logged on" also works if you stay logged in.
- **Working directory:** the script `cd`s to its own repo root, so the "Start in" field is optional (set above anyway).
  `schtasks` has no "Start in" option, which is why the script doesn't depend on it.
- **Execution policy:** `-ExecutionPolicy Bypass` on the command line applies to this process only. Don't change the
  machine policy. If the repo was downloaded as a zip, also run `Unblock-File .\scripts\nightly_update.ps1`.
- **Time:** the trigger uses local time. Leave "Synchronize across time zones" unticked, so 19:30 stays 19:30 local
  through DST.
- `-StartWhenAvailable` runs a missed fire after the PC comes back on. `-WakeToRun` wakes it from sleep. The latter
  also needs "Allow wake timers" enabled in the power plan.
- **Environment variables:** set them per user, for example
  `[Environment]::SetEnvironmentVariable('VALUATION_SEC_USER_AGENT', 'trade2-screener <you@example.com>', 'User')`.
  The task picks them up on its next start. If the task can't find uv, set `TRADE2_UV` to the full path of `uv.exe`
  (`(Get-Command uv).Source`).

## Weekly deep refresh (optional, off by default)

`--refresh-history` refetches 2 years of daily bars for every ticker. It makes the same number of requests as a normal
night (one chart call per ticker) but downloads far more, so it is off by default. The incremental policy already
does a full refetch when a dividend or split appears, or after a gap of more than 7 days. Use the deep refresh if you
suspect drifted adjusted closes. Two ways to enable it:

- Add a separate Saturday job that passes `--deep` / `-Deep`. For example, the crontab line
  `30 10 * * 6 /path/to/trade2/scripts/nightly_update.sh --deep >/dev/null 2>&1`, or a second Task Scheduler trigger
  with `-Deep` added to the arguments.
- Or set `TRADE2_DEEP_REFRESH_DAY=5`, so the regular Friday run goes deep. This works because the weekday schedule
  already runs on that day.

Fundamentals have their own flag (`--refresh-fundamentals`, a full refetch). Run it manually after a schema or data
batch change, not nightly.

## Verify it ran

- **Log:** `logs/nightly_<date>.log` ends with `=== trade2 nightly update OK: reports/<date>/ ===`, or with
  `FAILED (exit N): <steps>`. Each ingest step prints a summary line
  (`ingested N tickers (... priced ...), K errors in Ns`). Per-ticker errors don't fail the step. The next night
  re-fetches them.
- **Report:** `reports/<date>/screen_signals.csv` exists with today's date.
- **Database:** the latest fetch per source in `fetch_log`:
  ```bash
  uv run python -c "import duckdb; print(duckdb.connect('data/valuation.duckdb', read_only=True).sql(\"SELECT source, max(fetched_at), count(*) FROM fetch_log GROUP BY 1 ORDER BY 2 DESC\"))"
  ```
- **Scheduler:** `systemctl --user list-timers` and `journalctl --user -u trade2-nightly`; on macOS,
  `launchctl print gui/$(id -u)/ca.trade2.nightly` (`last exit code`); on Windows,
  `Get-ScheduledTaskInfo 'trade2 nightly update'` (`LastRunTime`, `LastTaskResult`, where 0 means OK).

## Run manually / disable

| | Run now | Disable | Remove |
|---|---|---|---|
| cron | `scripts/nightly_update.sh` | comment out the line (`crontab -e`) | delete the line |
| systemd | `systemctl --user start trade2-nightly.service` | `systemctl --user disable --now trade2-nightly.timer` | also remove the two files from `~/.config/systemd/user/` |
| launchd | `launchctl kickstart gui/$(id -u)/ca.trade2.nightly` | `launchctl bootout gui/$(id -u)/ca.trade2.nightly` | also delete the plist from `~/Library/LaunchAgents/` |
| Windows | `Start-ScheduledTask 'trade2 nightly update'` | `Disable-ScheduledTask 'trade2 nightly update'` | `Unregister-ScheduledTask 'trade2 nightly update'` |

## Common failures

| Symptom in the log | Cause | Fix |
|---|---|---|
| `HTTP Error 429` / `Too Many Requests`, many per-ticker errors | Yahoo throttling (shared IP, VPN, or another tool hitting Yahoo at the same time) | The step retries once after 5 minutes, and the next night catches up. If it happens every night, move the schedule or raise `TRADE2_RETRY_DELAY`. |
| `uv not found`, exit 127 | The scheduler's minimal PATH | Set `TRADE2_UV` to the full path, or set PATH in the crontab, unit, plist or user environment (see each OS section). `--dry-run` shows what the script sees. |
| No log for a weekday | Machine asleep or off; on Linux, user timer without linger; on macOS, repo under `~/Documents` (TCC) | Wake timers (`pmset`, `-WakeToRun`), `Persistent=true` / `-StartWhenAvailable`, `loginctl enable-linger`, move the repo |
| `Could not set lock on file ... data/valuation.duckdb` / `IO Error: ... lock` | Another process has the DuckDB file open (a notebook, a DuckDB CLI, a manual `valuation` run) | DuckDB allows one writer. Close the other process, or open it with `read_only=True`. The retry covers short overlaps. |
| `SKIP: another nightly update is running` | A previous run is still going (first full ingest, slow network) | Nothing to do. A lock left by a crashed run is detected (dead PID; on Windows the OS releases it) and taken over. |
| `Operation not permitted` (macOS) | TCC privacy protection | Move the repo out of `~/Documents` etc., or grant Full Disk Access |
| `running scripts is disabled on this system` (Windows) | Execution policy | Keep `-ExecutionPolicy Bypass` in the task arguments. `Unblock-File` for downloaded files. |
| `value` skipped, no report today | An ingest step failed twice | Read the ingest output above it in the log, then rerun manually once the cause is fixed. |
