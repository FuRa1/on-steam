# Operations runbook

Everything needed to run, debug and rebuild this setup, including what was verified by hand on
the real devices (2026-11-08). Setup steps and background are in the [README](../README.md);
this file covers day-to-day commands, where things live, and what works and what doesn't.

**Secrets are not in the repo.** `.env` (with `SHARED_SECRET`) is git-ignored. It lives on the
workstation (`C:\workspace\on-steam\.env`) and on the helper (`~/tv-wake-launcher/.env`, mode 600).
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
Tests: on the helper `cd agent && .venv/bin/python -m pytest -q` (63 tests);
`node tv-app/tests/hmac-test.js`.

## 4. SSH to the TV: what is allowed (webOS 26, verified)

Login: `ssh -i ~/.ssh/tvwake_tv -p 9922 -o PubkeyAcceptedAlgorithms=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa prisoner@192.168.1.111`
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
| `pc\setup-windows.ps1` (this repo) | run once as admin; already done on MoralMe (Private network, ping rule, Fast Startup off, NIC wake, no sign-in after sleep, password sign-in allowed) |
| Steam | in autostart (`-silent`), remembers login |

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
- [ ] **Real wake from shutdown/sleep through the app**: not yet tested end to end (WoL from
      the L590 worked in an earlier setup). BIOS ErP status not checked.
- [ ] **Windows auto-login**: Autologon did not work (Microsoft account). Without it, after a cold
      boot you log in on the streamed login screen; Steam starts after that.
- [ ] **Image quality**: Moonlight defaulted to 10 Mbit/s, and Apollo captured the 3440x1440 monitor.
      To do: Moonlight 4K, 60 (or 120) fps, HEVC, 50–80 Mbit/s; Apollo → Steam Big Picture →
      "Always use Virtual Display". Verify in `sunshine.log` (`Capture size`, `Client Requested bitrate`).
- [ ] **Direct start into Big Picture**: needs the numeric app ID from Moonlight's app info
      dialog; then in `.env`:
      `STREAM_APP_PARAMS='{"host_uuid":"A4ED6DC5-4E97-DA21-C08C-416F2459E2BF","host_app_id":<ID>}'`
      and `scripts/deploy.sh tv`.
- [ ] Controller: none yet. Connect it to the **TV** (Bluetooth/USB); ViGEmBus on the PC is installed.
