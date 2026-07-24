# Scheduling the weekly cookie_fetch

`sift run --source cookie_fetch` pulls only NEW saves (gallery-dl `--download-archive`),
read-only, from your machine and home IP. Weekly is the default cadence by design
(DESIGN.md §4). **Over-scheduling is safe:** the cadence guard in code refuses any run
< 20h after the last successful one, every attempt — even a failed one (expired
cookies, extractor breakage) — starts a 4h floor before the next real launch, a 429
back-off pause refuses runs until it expires, and a lock refuses a second simultaneous
run — a misconfigured hourly job produces refusals, never an hourly stream of real
requests, even while the session is broken.

Both recipes assume the repo lives at `~/sift` with its `.venv`; adjust paths.

## launchd (macOS)

Save as `~/Library/LaunchAgents/com.sift.cookie-fetch.plist`, then
`launchctl load ~/Library/LaunchAgents/com.sift.cookie-fetch.plist`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sift.cookie-fetch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/sift/.venv/bin/sift</string>
    <string>run</string>
    <string>--source</string>
    <string>cookie_fetch</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/YOU/sift</string>
  <key>StartCalendarInterval</key>
  <dict>
    <!-- Sundays 10:00 local time; launchd runs missed jobs on next wake -->
    <key>Weekday</key><integer>0</integer>
    <key>Hour</key><integer>10</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/tmp/sift-cookie-fetch.log</string>
  <key>StandardErrorPath</key><string>/tmp/sift-cookie-fetch.log</string>
</dict>
</plist>
```

## cron (any Unix)

```cron
0 10 * * 0  /Users/YOU/sift/.venv/bin/sift run --source cookie_fetch >> /tmp/sift-cookie-fetch.log 2>&1
```

Notes:
- The job must run as your user (it reads your browser cookies via
  `--cookies-from-browser`; `SIFT_COOKIE_BROWSER`, default chrome).
- `SIFT_IG_USERNAME` and `SIFT_VAULT_PATH` come from `.env` in the working
  directory (or the environment).
- cron does not run missed jobs while the machine sleeps — on a laptop prefer
  launchd, which catches up on wake.

# Scheduling the daily media backfill (`sift fetch-media`)

After a bulk import with `SIFT_MEDIA_DEFER=true` (`sift run --source dyi_export
--defer-media`), Instagram items wait in the SQLite `media_queue`
(`<work_dir>/ledger.db`) instead of getting caption-only analysis. `sift
fetch-media` drains one even chunk per run (`SIFT_MEDIA_CHUNK_SIZE`, default 100):
it downloads the real media, then runs the single full analysis per item (OCR/STT
included). Schedule it daily until the queue is empty; once drained it becomes a
clean no-op, so leaving the job in place is safe.

**Over-scheduling is safe here too:** chunks share cookie_fetch's session lock and
429 back-off pause (a rate limit in either pauses both), and a 4h attempt floor
refuses rapid re-runs. Audit progress any time:

```sh
sqlite3 "<work_dir>/ledger.db" "SELECT status, COUNT(*) FROM media_queue GROUP BY status"
```

## launchd (macOS)

Save as `~/Library/LaunchAgents/com.sift.fetch-media.plist`, then
`launchctl load ~/Library/LaunchAgents/com.sift.fetch-media.plist`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sift.fetch-media</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/sift/.venv/bin/sift</string>
    <string>fetch-media</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/YOU/sift</string>
  <key>StartCalendarInterval</key>
  <dict>
    <!-- daily 09:00 local; offset from the weekly cookie-fetch job so the shared
         session lock never makes one refuse the other -->
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/tmp/sift-fetch-media.log</string>
  <key>StandardErrorPath</key><string>/tmp/sift-fetch-media.log</string>
</dict>
</plist>
```

## cron (any Unix)

```cron
0 9 * * *  /Users/YOU/sift/.venv/bin/sift fetch-media >> /tmp/sift-fetch-media.log 2>&1
```
