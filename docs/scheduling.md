# Scheduling the weekly cookie_fetch

`sift run --source cookie_fetch` pulls only NEW saves (gallery-dl `--download-archive`),
read-only, from your machine and home IP. Weekly is the default cadence by design
(DESIGN.md §4). **Over-scheduling is safe:** the cadence guard in code refuses any run
< 20h after the last successful one, and a 429 back-off pause refuses runs until it
expires — a misconfigured hourly job just produces refusals, never extra requests.

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
