# Operations runbook

Everything needed to run, debug and rebuild this setup, including what was verified by hand on
the real devices (2026-11-08). Setup steps and background are in the [README](../README.md);
this file covers day-to-day commands, where things live, and what works and what doesn't.

**Secrets are not in the repo.** `.env` (with `SHARED_SECRET`) is git-ignored. It lives on the
workstation (`C:\workspace\on-steam\.env` on the PC; the MacBook checkout keeps it as `env`, untracked) and on the helper (`~/tv-wake-launcher/.env`, mode 600).
If both copies are lost: `cp .env.example .env`, `scripts/gen-secret.sh --write`, fill in the
values below, then `scripts/deploy.sh agent tv`.

---

## 1. The setup

| Device | Name | IP (DHCP reservation) | MAC | Notes |
|---|---|---|---|---|
| Router | — | 192.168.1.254 | — | main router; static leases for everything below |
| Helper | ThinkPad L590 (`akeya-ThinkPad-L590`), ssh alias `l590` | 192.168.1.155 | 00:2b:67:87:ee:d4 | Ubuntu 24.04, Python 3.12, user `akeya`. NIC `enp0s31f6` (Intel I219-LM) links at **100 Mb/s** (cable/switch port?). Also has tailscale, wg0, docker interfaces, so the agent binds to the LAN IP only. |
| Gaming PC | `MoralMe` (MSI MS-7C02, RTX 3090), Windows 11 | 192.168.1.132 | 2c:f0:5d:e6:1c:ee | Realtek GbE, 1 Gb/s. Monitor 3440x1440 @ 100 Hz. Windows user `grevt` = Microsoft account. |
| TV | LG 75QNED8MB6A, webOS 26, fw 43.22.17 | 192.168.1.111 | d0:cd:bf:ed:48:84 (wired) | Panel 4K, **144 Hz**, VRR (from `tv.hw.panelOutputFrameRate`). ares device name `lgtv`. |
| TP-Link ER605 | VPN router (WireGuard client) | 192.168.1.232 (WAN side) | b8:fb:b3:2d:4a:6f | **The PC must not sit behind it**: it NATs its own 192.168.0.x network, so WoL and the checks cannot reach the PC. The PC was moved to a switch on the main router. |

Streaming: **Moonlight TV** (`com.limelight.webos` v1.6.36) on the TV → **Apollo** 0.4.6
(Sunshine fork, Windows service `ApolloService`) on the PC. Moonlight is paired with Apollo
(host `MoralMe`, uniqueid `A4ED6DC5-4E97-DA21-C08C-416F2459E2BF`). There is no Steam Link app
for webOS.

## 2. What happens when you press "PC Game" on the TV

1. The TV app (`com.home.tvwake`, file:// web app) calls `GET http://192.168.1.155:8787/health`.
2. `POST /wake` (HMAC-signed) → the agent on the L590 starts or joins a job:
   - **pc_up**: ping 192.168.1.132 + TCP connect to 47989 (Apollo). Port open → done, skip to 6.
   - **wake**: 3 magic packets to `2c:f0:5d:e6:1c:ee` via 192.168.1.255, UDP 9 and 7.
   - **boot**: 30 s without checks, then every 4 s ping/port, up to 150 s; WoL re-sent every 20 s.
   - **stream**: TCP 47989 every 4 s, up to 90 s.
3. The TV polls `GET /jobs/<id>` every 1.5 s and shows the stages.
4. On "ready" the TV launches Moonlight via Luna (`applicationManager/launch`, needs ACG
   `application.launcher`) and closes itself after 3 s.
5. In Moonlight: MoralMe → *Steam Big Picture* or *Desktop*. (Direct start: see §8.)

Same flow without the TV: `scripts/agent-cli.sh wake` (on the helper).

## 3. Command cheat sheet

All on the helper (`ssh l590`, then `cd ~/tv-wake-launcher`) unless noted.

### Agent and timer

```sh
systemctl --user status tvwake-agent            # API service
journalctl --user -u tvwake-agent -f            # live log (wake jobs, rejected requests, X-Client)
systemctl --user restart tvwake-agent
systemctl --user list-timers tvwake-devmode.timer
journalctl --user -u tvwake-devmode -n 50       # dev mode renewal runs
scripts/agent-cli.sh health | wake | devmode    # talk to the agent like the TV does
```
Units are **user units** (`~/.config/systemd/user/`), linger is on (`loginctl show-user akeya -P Linger` → yes),
so they run without a login and need no sudo.

### PC

```sh
scripts/pc-on.sh                                 # WoL + wait until it answers
scripts/agent-cli.sh wake                        # full flow with stages
ping -c1 192.168.1.132                           # works after pc/setup-windows.ps1 (firewall rule)
nc -zv 192.168.1.132 47989                       # Apollo up?
curl -s http://192.168.1.132:47989/serverinfo    # Apollo host info (hostname, uniqueid, state), no auth
```

### TV

```sh
scripts/tv-on.sh                                 # WoL to the TV (works, ~1 s)
scripts/tv-off.sh                                # off via LG control protocol (port 3001, paired)
scripts/devmode.sh status | tick | renew         # dev mode session
export PATH=$HOME/.local/bin:$PATH               # webOS CLI (ares-*)
ares-setup-device -l                             # device "lgtv"
ares-device -i -d lgtv                           # model, firmware
ares-install -d lgtv -l                          # apps installed via dev mode
ares-launch -d lgtv com.home.tvwake              # start our app
ares-launch -d lgtv com.limelight.webos          # start Moonlight
ares-launch -d lgtv -c com.home.tvwake           # close an app
ares-launch -d lgtv -r                           # running apps
```

### Build and deploy

From the workstation (Git Bash in `C:\workspace\on-steam`):
```sh
scripts/deploy.sh agent        # copy repo + .env to the helper, (re)install agent + timer
scripts/deploy.sh tv           # build the IPK on the helper and install it on the TV
scripts/deploy.sh agent tv     # both
```
Tests: on the helper `cd agent && .venv/bin/python -m pytest -q` (65 tests);
`node tv-app/tests/hmac-test.js`.

### From the MacBook (verified 2026-11-09)

The MacBook (`192.168.1.79` at home, Tailscale everywhere) reaches all three devices:

```sh
ssh l590                                         # helper (Tailscale)
ssh moralme                                      # gaming PC, user grevt, admin shell (Windows OpenSSH, Tailscale)
ssh lgtv                                         # TV directly (home LAN only)
ssh lgtv-remote                                  # TV through the helper (ProxyJump l590), works away from home
ssh -tt lgtv 'luna-send-pub ...'                 # luna-send-pub needs a TTY
```
`lgtv`/`lgtv-remote` are entries in `~/.ssh/config` (port 9922, user `prisoner`, `+ssh-rsa`) with the
key `~/.ssh/tvwake_tv` copied from the helper. PowerShell over ssh to the PC: quoting through cmd breaks
easily, so pipe the script on stdin: `ssh moralme 'powershell -NoProfile -Command -' <<'EOF' ... EOF`.
Don't run GUI tools over that ssh (e.g. `autologon /?` opens an invisible window and hangs).

**TV On / TV Off in the Shortcuts app**: `mac/make-tv-shortcuts.sh` builds both, signs them
(`shortcuts sign`) and opens them for import (click "Add Shortcut"). They run `tv-on.sh` / `tv-off.sh`
on the helper over ssh. Needs Shortcuts → Settings → Advanced → "Allow Running Scripts".

## 4. SSH to the TV: what is allowed (webOS 26, verified)

Login: `ssh -i ~/.ssh/tvwake_tv -p 9922 -o PubkeyAcceptedAlgorithms=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa prisoner@192.168.1.111`
(from the MacBook: `ssh lgtv`)
(user `prisoner`, group `jailer`; the TV only offers old ssh-rsa).

**`luna-send-pub` prints its reply only with a terminal**: always use `ssh -tt` for Luna
calls, otherwise you get exit code 0 and no output.

| Action | Command | Result |
|---|---|---|
| Read dev mode token | `cat /var/luna/preferences/devmode_enabled` | ✅ 64 chars |
| Extend dev mode session | `luna-send-pub -n 1 luna://com.webos.applicationManager/launch '{"id":"com.palmdts.devmode","params":{"extend":true}}'` | ✅ `{"returnValue":true}`, timer back to 1000 h |
| Launch any app | `luna-send-pub -n 1 luna://com.webos.applicationManager/launch '{"id":"<app id>"}'` | ✅ |
| Read TV hardware config | `luna-send-pub -n 1 luna://com.webos.service.config/getConfigs '{"configNames":["tv.model.*","tv.hw.*"]}'` | ✅ (panel 144 Hz, VRR, etc.) |
| List apps / launch points | `applicationManager/listApps`, `listLaunchPoints` | ❌ no reply (denied) |
| Foreground app | `applicationManager/getForegroundAppInfo` | ❌ `Denied method call` |
| Power state / power off | `com.webos.service.tvpower/power/getPowerState`, `powerOff` | ❌ `Denied method call` → use `scripts/tv-off.sh` |
| Ethernet link speed | `cat /sys/class/net/eth0/speed` | ❌ `Invalid argument` |

### Other ways to control the TV

| Way | Used for | Notes |
|---|---|---|
| Wake-on-LAN to `d0:cd:bf:ed:48:84` | **on** (`scripts/tv-on.sh`) | Works from standby. TV setting "Turn on via Wi-Fi/LAN" must stay on. |
| LG control protocol, wss port 3001 (SSAP) | **off** (`scripts/tv-off.sh`) | Library `aiowebostv` (agent/requirements-tv.txt). Paired once (prompt accepted); key in `~/.local/state/tvwake/tv_client_key`. If the key is lost: delete the file, run tv-off.sh, accept the prompt. |
| webOS CLI (`ares-*`) | install, launch, close apps | Uses the same SSH key (`~/.ssh/lgtv_webos`, passphrase in ares config). |

With Quick Start+ the TV keeps answering on the network (port 3001, sometimes 9922) while it is "off".

## 5. Developer Mode session

- Session length: **1000 h** (about 41 days). Shown in the TV app footer ("Dev mode: N days left") and
  by `scripts/devmode.sh status`.
- The timer (`tvwake-devmode.timer`) runs every 5 min. It runs **extend** when the TV comes on and the last
  extend is > 7 days old (or > 14 days in any case), a **cloud reset** every 24 h (works with the TV off,
  using the cached token), and a **check** every 6 h.
- The token cache is `~/.local/state/tvwake/devmode_token`; the state is `devmode.json` in the same dir.
- If the session ever expires: Developer Mode and our apps (PC Game, Moonlight) stop working.
  Open the Developer Mode app on the TV, sign in to the LG developer account, enable it again,
  then `scripts/devmode.sh renew`, and reinstall: `scripts/deploy.sh tv` plus Moonlight (§7).

## 6. Files on each device

**Helper (L590)**

| Path | What |
|---|---|
| `~/tv-wake-launcher/` | deployed copy of this repo (deploy.sh overwrites it) |
| `~/tv-wake-launcher/.env` | config with the secret (600) |
| `~/tv-wake-launcher/agent/.venv/` | Python venv (fastapi, uvicorn; aiowebostv for tv-off) |
| `~/.config/systemd/user/tvwake-*` | systemd units (rendered from `systemd/*.in`) |
| `~/.local/state/tvwake/` | `devmode.json`, `devmode_token`, `tv_client_key` (700/600) |
| `~/.ssh/lgtv_webos` | TV key as downloaded by ares (encrypted; its passphrase is shown in the TV's Developer Mode app and stored in the ares device config) |
| `~/.ssh/tvwake_tv` | the same key without passphrase, used by the agent (600) |
| `~/.webos/tv/novacom-devices.json` | ares device list (`lgtv`) |
| `~/.local/bin/ares*` | webOS CLI 3.2.6 (`npm install -g --prefix ~/.local @webos-tools/cli`) |
| `~/tvwake-dl/moonlight-1.6.36/app.ipk` | Moonlight TV installer (sha256 `74d9788b…8824`, matches the release manifest) |

**Gaming PC**

| Path / item | What |
|---|---|
| `C:\Program Files\Apollo\` | Apollo; `config\sunshine.log` (log), `config\apps.json` (Desktop, Steam Big Picture) |
| https://localhost:47990 | Apollo web UI (admin login; PIN pairing; Clients → permissions; Applications) |
| Service `ApolloService` | starts automatically, before login |
| Driver `ViGEmBus` | virtual Xbox controller for controllers connected to the TV |
| Desktop shortcuts **TV On** / **TV Off** | run `pc\tv-power.cmd on\|off` → `ssh l590` → `scripts/tv-on.sh` / `tv-off.sh`. Recreate with `powershell -ExecutionPolicy Bypass -File pc\make-shortcuts.ps1` (uses Windows OpenSSH and the `l590` alias from `%USERPROFILE%\.ssh\config`; override with env `TVWAKE_HELPER_SSH`). |
| `pc\setup-windows.ps1` (this repo) | run once as admin; already done on MoralMe (Private network, ping rule, Fast Startup off, NIC wake, no sign-in after sleep, password sign-in allowed) |
| Steam | Run entry `steam.exe -silent`, remembers login. **But** (2026-11-09) every HKCU Run entry is marked disabled in `StartupApproved\Run` (value `01 00 00 00`), so after auto-login nothing from it starts, Steam included. Fix: Task Manager → Startup apps → Steam → Enable, or a logon task in Task Scheduler. |
| Auto-login | Sysinternals Autologon (winget), set with `pc\set-autologon.ps1`: user `grevtsov.b@protonmail.com`, domain `MicrosoftAccount`, password stored as an LSA secret. Off again: `set-autologon.ps1 -Disable`. |
| `config\sunshine.conf` | `dd_configuration_option = ensure_only_display`, `headless_mode = enabled`, `dd_config_revert_on_disconnect = enabled` (see §8) |

**TV**: apps `com.home.tvwake` ("PC Game") and `com.limelight.webos` (Moonlight), both installed
through Developer Mode.

## 7. Recovery procedures

| Situation | Fix |
|---|---|
| TV app shows "rejected this app" | Secret mismatch: `scripts/deploy.sh agent tv`. An `.env` with CRLF line endings caused this once; the scripts strip CR now. |
| Helper reinstalled / new helper | Install Node + `@webos-tools/cli`, clone the repo, put `.env` in place, `scripts/setup-tv-key.sh` (Key Server ON on the TV), `scripts/install-agent.sh --test`, `scripts/deploy.sh tv` from the workstation. |
| TV key lost / TV reset | TV: Developer Mode app → Key Server ON; helper: `scripts/setup-tv-key.sh` (enter the passphrase shown). |
| Moonlight missing | `ares-install -d lgtv ~/tvwake-dl/moonlight-1.6.36/app.ipk` (or a newer release from github.com/mariotaku/moonlight-tv; check the sha256 against its manifest). Then pair again (Moonlight shows a PIN → Apollo web UI → PIN). |
| Moonlight "Permission denied" on launch | Apollo web UI → Clients → the TV → enable *Launch Apps* (and input). |
| PC unreachable from the helper | Check that it is on the main network (192.168.1.x), not behind the ER605 (192.168.0.x). |
| Changing IPs / devices | Edit `.env`, `scripts/deploy.sh agent tv`. |

## 8. Status and open items (2026-11-08)

Done and verified:
- Agent + timer running on the L590 (real PC, `MOCK_PC=0`); mock mode tested end to end on the TV.
- TV app installed; WebCrypto signing works on webOS 26 (`crypto=webcrypto` in the agent log).
- Detecting the PC as already on → Moonlight launch → app closes itself: works.
- Dev mode renewal (extend + cloud reset + check): works, 1000 h.
- TV on (WoL) and off (SSAP): work.
- Streaming Steam Big Picture through Apollo: works.

Open:
- [ ] **Real wake from shutdown/sleep through the app**: not yet tested end to end. WoL from a full
      shutdown via `scripts/pc-on.sh` works (2026-11-09: answers after 25 s, auto-login OK). BIOS ErP status not checked.
- [x] **Windows auto-login** (fixed 2026-11-09). The first attempt failed because Autologon got the PIN
      instead of the Microsoft account password (Security log: 4625, `0xC000006A`), and Windows then
      reset `AutoAdminLogon`. Now set with `pc/set-autologon.ps1` (checks the password with `LogonUser`
      first; user = account e-mail, domain = `MicrosoftAccount`). BitLocker is TPM-only, so no pre-boot PIN.
- [x] **Image quality** (fixed 2026-11-08). The starting state was Moonlight at 1080p / 10 Mbit/s and Apollo capturing
      the 3440x1440 monitor, with Big Picture opening on the monitor. The working setup:
      - Moonlight: **3840x2160, 60 fps, 42 Mbit/s** (since 2026-11-09), H.265 on, AV1 off (the RTX 3090
        cannot encode AV1), HDR off. 65 Mbit/s is the maximum Moonlight allows on webOS.
      - **Never 144 fps** (Moonlight offered it from the panel's 144 Hz): the TV's decoder cannot keep up
        with 4K 144 at 65 Mbit/s, frames queue up and the picture lags further and further behind (minutes
        after a while: "the input is recorded and played back later"). It looks like gamepad lag but is
        video. Apollo then also logs `NvEnc: frame N encode wait timeout`. At 4K 60 / 42 Mbit/s: no lag,
        no encoder errors.
      - Apollo → Applications → Steam Big Picture → **Always create Virtual Display** (`"virtual-display": true`).
      - Apollo → Audio/Video → Advanced display device options → **Device configuration =
        "Deactivate other displays and activate only the specified display"**
        (`dd_configuration_option = ensure_only_display` in `sunshine.conf`; changed 2026-11-09 from
        `ensure_primary`). With `ensure_primary` the monitor stayed on next to the virtual display
        (`Virtual Desktop : 7280x2160` in the log), so games sometimes opened on the monitor or lost focus
        and the TV did not show them full screen. Now the monitor is off during a stream and comes back
        when the app is quit.
      - Apollo → **Headless Mode = on** (`headless_mode = enabled`): every app starts in a virtual display.
      - Apollo → **Config revert on disconnect = on** (`dd_config_revert_on_disconnect = enabled`, 2026-11-09).
        **Does not help with a paused session** (tested): turning the TV off mid-game only *pauses* it,
        Apollo keeps the virtual display, and the monitor stays dark until the app is quit. That is why the
        PC now puts itself to sleep when a stream is over (§9); "PC Game" then resumes the same game.
        (A TV-side fix cannot work: the remote's power button kills the TV apps.)
      - Backup of the previous config: `config\sunshine.conf.bak-20261109`.
      - Verified in `sunshine.log`: `Capture size 3840x2160`, `Display refresh rate [120Hz]`; the virtual
        display is primary during the stream and the monitor stays on.
      - Start apps **from Moonlight**, not from Apollo's web UI. A web-UI launch creates the virtual display
        in the fallback mode (1920x1080x60), and the TV then *resumes* that session. If it happens:
        Quit the app in Moonlight and start it again.
- [ ] **Steam does not start after auto-login** (cause found 2026-11-09): all HKCU Run entries are
      disabled in `StartupApproved` (see §6). Enable Steam in Task Manager → Startup apps, or add a
      logon task. Until then the agent's "ready" (Apollo port) comes before Steam.
- [x] **PC left on after the TV is switched off** (2026-11-09): `pc/events.ps1`, see §9. Verified by
      hand: TV off → paused session → PC sleeps → "PC Game" → WoL wakes it in ~1.5 s, agent ready
      9 s after the button, Moonlight **resumes the same game**.
      Windows' own sleep timer can't do it: a game holds `USB Audio Device` / `Legacy Kernel Caller`
      power requests even while paused.
- [ ] Agent readiness: Apollo's port opens at the login screen, before Steam. Optionally also wait for
      Steam (port 27036) before launching Moonlight.
- [ ] **Direct start into Big Picture**: needs the numeric app ID from Moonlight's app info
      dialog; then in `.env`:
      `STREAM_APP_PARAMS='{"host_uuid":"A4ED6DC5-4E97-DA21-C08C-416F2459E2BF","host_app_id":<ID>}'`
      and `scripts/deploy.sh tv`.
- [x] Controller (2026-11-09): connected to the TV, works through Apollo (ViGEmBus running; Apollo logs
      `Gamepad N will be Xbox 360 controller`; client "LG Apollo" has all input permissions, perm 0x71F1F00).
      Moonlight reports 3 pads: 2 x Xbox 360 + a "DualShock 4" chosen by motion sensor (probably the Magic
      Remote). Works as is; if Steam picks the wrong player, look at that.

## 9. PC events: Telegram + sleep when a stream is over (2026-11-09)

Everything runs **on the PC**, driven by events; nothing polls. The helper is never put to sleep.

| Event | Fired by | Telegram |
|---|---|---|
| app started / closed | Apollo `global_prep_cmd` do / undo | `stream.start` / `stream.end` (+ session length) |
| first client connected / all disconnected | Apollo `global_state_cmd` do / undo | `stream.resume` / `stream.pause` |
| a Steam game starts during a stream | watcher in the start/resume worker: sleeps on `RegNotifyChangeKeyValue` (`HKCU\Software\Valve\Steam`, `RunningAppID`) and on the named event `Global\TvWakeStreamEnded` that the end hook sets; no timer | `game.start <name>` (no game.end, on purpose) |
| stream over (pause or app closed), nothing restarts within 30 s | the pause / end hook | `pc.sleep`, then S3 sleep (TV on or off) |
| same, but a Steam game is still running | waits `AUTOSLEEP_GAME_MIN` (15) instead of 30 s, then **forced shutdown** (`shutdown /s /f`): a game can't always be closed (elevated / anti-cheat), and many hang after S3 | `pc.off <name> left running, shutting down` |
| boot | Task Scheduler `\TvWake\Boot` (startup + 30 s) | `pc.on cold boot · signed in after N s` (or "nobody signed in after 2 min"); `pc.crash` first if 6008 / Kernel-Power 41 since boot |
| wake | `\TvWake\Wake` (Power-Troubleshooter 1) | `pc.wake asleep since HH:MM · by network (Wake-on-LAN)` / power button / keyboard or mouse |
| shutdown | `\TvWake\Shutdown` (User32 1074) | `pc.off <type> · <Windows Update / Start menu, user / process, user>`; quiet when events.ps1 shut down itself |
| Apollo service crashed | `\TvWake\ApolloCrash` (SCM 7031/7034; Windows 11 logs no plain 7036 stop events) | `apollo.down Apollo service crashed · restarted by Windows / still stopped` |
| PC woken from the TV | the agent on the helper (`agent/tvwake/notify.py`) | `pc.ready` (in N s) / `pc.error` |
| dev mode renewal fails or < 100 h left | `tvwake-devmode` timer on the helper | `tv.devmode <error> · N h left` (at most every 12 h), then once `renewed again` |
| the helper booted | agent start within 10 min of boot | `helper.up L590 booted N min ago` |

Details in stream messages: `stream.start/resume` carry the mode the client asked for (`4K 144 Hz`, from
`APOLLO_CLIENT_*`) and `sleep/shutdown cancelled` when someone came back during the wait (the start/resume
hook sets the named event `Global\TvWakeStreamBack`, which ends the wait at once). `stream.end` carries
the length, `played <games>`, the codec and the number of encoder errors (Apollo log since the start).
Messages from a test folder (`-Dir`) end in `(test)`.

Message style ("Terminal + color"): `MARK <code>ICON key</code> details`, Telegram HTML. Marks: 🔵 stream
running, ⚪ stopped, 🟣 PC power, 🟢 ok, 🔴 error. Telegram shows `<code>` as monospace only (no color);
the marks carry the color. `events.ps1` is pure ASCII (symbols built from code points), so it works
with or without a BOM.

- **Always parse-check before installing** (`[System.Management.Automation.Language.Parser]::ParseFile`):
  a script that does not parse makes the start hook fail, and Apollo then does not launch the app.
- Install / update on the PC (elevated): `powershell -ExecutionPolicy Bypass -File pc\install-events.ps1`
  (settings from `.env`: `TELEGRAM_*`, `AUTOSLEEP` off|dry-run|on, `AUTOSLEEP_AFTER_MIN`, `AUTOSLEEP_GAME_MIN`).
  `-Uninstall` reverts everything. Log: `C:\ProgramData\tvwake\events.log`.
- Sleep only happens in the pause/end hook, so a PC woken by hand is never put back to sleep (the earlier
  polling version did exactly that: it trusted the wake time, see below). "Is a stream back?" = the
  number of `New streaming session` / `Session resuming` lines in Apollo's log changed during the 30 s
  (a restarted Apollo starts a new log, which also counts as "don't sleep").
- Known gap: a PC woken but never streamed to stays on (no stream, no hook).
- **Games and S3** (seen 2026-11-09 with INSIDE): after waking, a game left running can hang (0 % CPU,
  black picture on the TV and the monitor) because the GPU was powered off and the game does not
  rebuild its device; exclusive fullscreen on Apollo's virtual display makes it worse. Big Picture
  itself survives. Hence the game timeout and shutdown above. Non-Steam games are not detected (the
  PC sleeps as if no game ran). Borderless/windowed fullscreen in a game's settings
  makes it more likely to survive a short sleep.
- Apollo waits for its hook commands, so the hook starts a detached copy through **WMI**
  (`Win32_Process.Create`). Processes started by a short-lived parent otherwise die with it: a sleep
  started with `Start-Process` from an ssh session never happened.
- **Kernel-Power 107 ("resumed from sleep") is stamped with the time the PC went to sleep**, not the
  wake time. The real wake time is Power-Troubleshooter 1.
- Windows PowerShell reads a .ps1 without a BOM in the ANSI code page; the UTF-8 bytes of some symbols
  (e.g. ✓ → `“`) then act as quote characters and break the script. Hence pure ASCII in `events.ps1`.
- Test without Apollo: copy `events.ps1` and an `events.json` (autosleep `dry-run`) to a folder and run
  `events.ps1 <start|end|pause|test> -Dir <folder>` with `APOLLO_APP_NAME` set; `-Run` runs a worker
  directly. Steam games can be simulated by writing `RunningAppID` (restore it afterwards).
- Steam can leave a stale `RunningAppID` / `Running=1` after it was closed; the watcher only reports
  changes after it started.
- The Telegram bot is the one Uptime Kuma uses (helper, http://akeya-thinkpad-l590:3001 → Settings →
  Notifications). The token is in `.env` on the MacBook (`env`), the helper and the PC; never commit it.
- `QUIET_PERIOD_S=0`: the agent starts checking right after WoL (a sleeping PC answers in seconds).

