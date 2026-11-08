# tv-wake-launcher

Press one icon on an LG TV and end up streaming games from your PC, even if the PC is
asleep or off.

```
 LG TV (webOS)                    Helper (Ubuntu, always on)              Gaming PC (Windows)
 ┌──────────────────┐  HTTP+HMAC  ┌──────────────────────────┐  WoL/ping   ┌──────────────────┐
 │ "PC Game" app    │ ──────────▶ │ agent (FastAPI, systemd) │ ──────────▶ │ Apollo/Sunshine  │
 │  shows progress, │             │  wake state machine      │  TCP 47989  │  (service)       │
 │  launches        │ ◀────────── │ devmode timer (5 min)    │             │ Steam            │
 │  Moonlight       │   SSH 9922  │  keeps Dev Mode alive    │             └──────────────────┘
 └──────────────────┘ ◀────────── └──────────────────────────┘
```

**Why Moonlight and not Steam Link:** there is no Steam Link app for LG webOS (Valve only made
it for Samsung, which was later discontinued, plus Apple TV and Android). The project uses
[Moonlight TV](https://github.com/mariotaku/moonlight-tv) on the TV and
[Apollo](https://github.com/ClassicOldSong/Apollo) (or Sunshine) on the PC. Steam Link support
is kept behind `STREAM_BACKEND=steamlink` in case it ever appears.

**Day-to-day commands, what is verified on the real devices, and where everything lives:
[docs/OPERATIONS.md](docs/OPERATIONS.md).**

## How it works

The TV app shows six stages:

| # | Stage | Who does it | What happens |
|---|-------|-------------|--------------|
| 1 | Connect to the helper | TV | `GET /health`. Fails with "helper is offline". |
| 2 | Is the PC already on? | agent | ping + TCP connect to the streaming port. Both up → skip to 6. |
| 3 | Wake the PC | agent | Wake-on-LAN magic packet, 3× to UDP 9 and 7. |
| 4 | Wait for the PC to start | agent | 30 s quiet, then poll every 4 s, up to 150 s. WoL is re-sent every 20 s. |
| 5 | Wait for the server | agent | Poll the streaming port (47989) every 4 s, up to 90 s. |
| 6 | Start Moonlight | TV | Luna `com.webos.applicationManager/launch`. |

The PC counts as "up" when it answers ping **or** the streaming port is open, so a Windows
firewall that blocks ping only costs a few seconds.

Separately, a systemd timer keeps the TV's **Developer Mode** session alive. An app installed
through Developer Mode stops working when the session runs out, and the TV app cannot renew
the session itself, so the helper does it (see [Developer Mode renewal](#4-developer-mode-renewal)).

## Repository layout

```
agent/tvwake/        config, auth (HMAC), clock, wol, probes, mock, wake (state machine),
                     devmode (renewal), api (FastAPI), __main__ (uvicorn)
agent/tests/         pytest: config, HMAC, state machine (fake clock), devmode, API
tv-app/              index.html, css/, js/ (app, api, hmac, luna, lib/sha256.js, lib/webOSTV.js),
                     appinfo.json.template, config.js.template, icons, tests/hmac-test.js
scripts/             deploy.sh, install-agent.sh, build-tv.sh, install-tv.sh, setup-tv-key.sh,
                     devmode.sh, gen-secret.sh, render_tv.py, make-icons.py, lib.sh,
                     agent-cli.sh (wake flow from a terminal), pc-on.sh, tv-on.sh, tv-off.sh
systemd/             agent service, devmode service + timer (templates)
pc/                  setup-windows.ps1 (one-time Windows setup, run as admin)
docs/OPERATIONS.md   runbook: commands, verified device facts, file locations, recovery
.env.example         every setting, commented
```

Everything device-specific is in `.env`. For another TV/PC/helper: edit `.env`, then
`scripts/deploy.sh agent tv`.

---

## 1. TV prerequisites

1. **LG developer account**: sign up at <https://webostv.developer.lge.com>.
2. On the TV, install **Developer Mode** from the LG Content Store, open it, log in, and switch
   **Dev Mode Status** on. The TV restarts.
3. **webOS CLI** on the helper (needs Node.js):
   ```sh
   npm install -g --prefix ~/.local @webos-tools/cli
   ares --version
   ```
   `~/.local/bin` must be on your `PATH` (it is by default on Ubuntu after you log in again).
4. **Register the TV and get its key.** In the Developer Mode app switch **Key Server** on, then
   on the helper:
   ```sh
   scripts/setup-tv-key.sh        # asks for the Passphrase shown in the Developer Mode app
   ```
   This adds the TV as ares device `TV_DEVICE_NAME`, downloads the key to
   `~/.ssh/<name>_webos`, and makes the passphrase-free copy `TV_SSH_KEY` the agent uses.
   Switch Key Server off afterwards.
5. Give the TV a **DHCP reservation** (use the MAC of the *wired* adapter if it is wired) and
   put that IP in `TV_IP`.

## 2. PC prerequisites (Windows 11)

**Network.** The PC must be on the **same network segment** as the helper. Wake-on-LAN is a
broadcast, and routers do not pass broadcasts on. A second router doing NAT in between
breaks it; a switch is fine. Give the PC a DHCP reservation (`PC_IP`, `PC_MAC`).

**BIOS / UEFI** (MSI boards: Settings → Advanced):
- Power Management Setup → **ErP Ready: Disabled** (otherwise the network card has no power when off)
- Wake Up Event Setup → **Resume By PCI-E Device: Enabled**

**Network adapter** (Device Manager → adapter → Advanced): *Wake on Magic Packet* = Enabled,
*Shutdown Wake-On-Lan* = Enabled. Power Management tab: *Allow this device to wake the computer*.

**Windows: the quick way.** Run `pc\setup-windows.ps1` in an *administrator* PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File pc\setup-windows.ps1
```
It covers everything below except automatic login, and prints what it changed. The manual
equivalent of the main steps, in an *administrator* PowerShell:
```powershell
# Home network should be "Private"
Set-NetConnectionProfile -InterfaceAlias Ethernet -NetworkCategory Private
# Allow ping from the local network (the agent uses it to see the PC boot)
New-NetFirewallRule -DisplayName "tv-wake: ping from LAN" -Direction Inbound -Protocol ICMPv4 -IcmpType 8 -RemoteAddress LocalSubnet -Profile Private -Action Allow
# Fast Startup off (it is a half-hibernate; WoL from "off" often fails with it on)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" /v HiberbootEnabled /t REG_DWORD /d 0 /f
```

**Automatic login**, so Steam/Big Picture can start without you typing a password:
1. Settings → Accounts → Sign-in options → turn off *For improved security, only allow Windows
   Hello sign-in…*
2. `Win+R` → `netplwiz` → untick *Users must enter a user name and password* → enter your password.
   (Or use Microsoft's [Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon).)
3. Sign-in options → *If you've been away, when should Windows require you to sign in again?* → **Never**.

Note: anyone with physical access to the PC gets into your account.

**Streaming server: Apollo** (or Sunshine):
1. Download the installer from the [Apollo releases](https://github.com/ClassicOldSong/Apollo/releases)
   and run it. `winget install ClassicOldSong.Apollo` only downloads the installer; you
   still have to run it.
2. Open <https://localhost:47990>, create the admin login.
3. Pair Moonlight on the TV (see section 5). In Apollo, the **first** paired client gets full
   permissions; later clients only get *View Streams* and *List Apps*, so grant them
   *Launch Apps* (and mouse/keyboard input) under Clients if needed.
4. Steam: keep it in autostart; Apollo has a "Steam Big Picture" app entry.

## 3. Helper setup (Ubuntu)

1. Give the helper a **DHCP reservation**; put its LAN IP in `HELPER_HOST`.
2. Copy `.env.example` to `.env` and fill it in. Create the secret:
   ```sh
   scripts/gen-secret.sh --write
   ```
3. Install the agent (venv, dependencies, systemd units, start):
   ```sh
   scripts/install-agent.sh --test     # --test also runs the unit tests
   ```
   By default the units are **user units** (`SYSTEMD_MODE=user`): no sudo, everything runs as
   you. "Linger" is enabled so they run without a login session. If that fails, run
   `sudo loginctl enable-linger $USER` once. `SYSTEMD_MODE=system` installs them system-wide
   with sudo instead.

   From a workstation you can do the copy + install over SSH instead:
   `scripts/deploy.sh agent` (needs `HELPER_SSH`).
4. Check it:
   ```sh
   curl http://HELPER_HOST:8787/health
   journalctl --user -u tvwake-agent -f
   ```

`ping` is the system binary. On Ubuntu it has the `cap_net_raw` capability, so it works
without root; a raw ICMP socket from Python would need root. For the same reason the service
does **not** use `NoNewPrivileges=`, which would strip that capability.

## 4. Developer Mode renewal

**Facts**, verified on a 2026 QNED with webOS 26 (firmware 43.22.17):
- A session lasts **1000 hours** (the Developer Mode app shows `999h:59m` right after extending).
- The session token is in `/var/luna/preferences/devmode_enabled` on the TV, readable over SSH
  as user `prisoner`, port 9922, with the key from `setup-tv-key.sh`.
- `GET https://developer.lge.com/secure/ResetDevModeSession.dev?sessionToken=<token>` answers
  `{"result":"success",...}`. Per the webosbrew maintainers this resets the timer on LG's
  server, but the TV only re-reads it at boot.
- `GET .../CheckDevModeSession.dev?sessionToken=<token>` answers
  `{"result":"success","errorCode":"200","errorMsg":"999:33:05"}`: the remaining time.
- Pressing **Extend Session Time** is the same as launching `com.palmdts.devmode` with
  `{"extend":true}`. Over SSH: `luna-send-pub -n 1 luna://com.webos.applicationManager/launch
  '{"id":"com.palmdts.devmode","params":{"extend":true}}'`. `luna-send-pub` only prints its
  reply when it has a terminal, so it is run with `ssh -tt`.
- Not allowed for `prisoner` on webOS 26: listing apps and `getForegroundAppInfo`.

**What the timer does** (`tvwake-devmode.timer`, every `DEVMODE_TICK_MIN` = 5 min):

| Condition | Action |
|---|---|
| TV reachable | read the token and cache it in `STATE_DIR` (mode 600) |
| TV just came on and the last extend was > `DEVMODE_EXTEND_INTERVAL_H` (7 days) ago, or the last extend was > 14 days ago | **extend** on the TV |
| last cloud reset > `DEVMODE_RENEW_INTERVAL_H` (24 h) ago | **cloud reset** with the cached token (works with the TV off) |
| after any action, or every `DEVMODE_CHECK_INTERVAL_H` | **check** remaining time, stored for the TV footer |

The run exits non-zero only when an action was attempted and failed, so `systemctl --user
status tvwake-devmode` and the journal show real failures. The endpoints are in `.env` in case
LG changes them.

**Commands:**
```sh
scripts/devmode.sh status      # remaining time, last result
scripts/devmode.sh renew       # extend + reset + check now
systemctl --user list-timers tvwake-devmode.timer
journalctl --user -u tvwake-devmode
```

If the session ever runs out, Developer Mode and every app installed through it stop. Open
the Developer Mode app on the TV, log in again, then reinstall the app (`scripts/deploy.sh tv`).

## 5. TV app: build, install, streaming app

**Build and install** (on the helper, where the webOS CLI is):
```sh
scripts/build-tv.sh            # generates tv-app/config.js + appinfo.json from .env, packages dist/*.ipk
scripts/install-tv.sh --launch
```
or from a workstation: `scripts/deploy.sh tv`.

**webOSTV.js**: `build-tv.sh` copies LG's library from the webOS CLI
(`@webos-tools/cli/files/templates/tv-sdk-templates/bootplate-web/webOSTVjs-*/webOSTV.js`) into
`tv-app/js/lib/`. You can also download it from
<https://webostv.developer.lge.com/develop/references/webostvjs-introduction>. Without it,
`js/luna.js` uses the `PalmServiceBridge` that webOS gives every app directly.

**Signing on the TV**: WebCrypto (`crypto.subtle`) **works** in the `file://` app context on
webOS 26: verified on a 2026 QNED, where the agent log shows `crypto=webcrypto` on each wake
request. `js/lib/sha256.js` is only a fallback for TVs where it does not. The footer and the
agent log show which one was used.

**Luna permission (ACG)**: on webOS 26+ an app that declares `requiredACG` may only call the
Luna methods of the groups it lists; webOS 27 makes the field mandatory. Launching another app
needs `application.launcher`, which is in `appinfo.json.template`.

**Moonlight TV on the TV:**
1. Download `com.limelight.webos_<version>_arm.ipk` from the
   [moonlight-tv releases](https://github.com/mariotaku/moonlight-tv/releases), and check its
   sha256 against `com.limelight.webos.manifest.json` from the same release.
2. `ares-install -d lgtv com.limelight.webos_<version>_arm.ipk`
3. Open Moonlight on the TV. It finds the PC. Pair it: it shows a PIN, enter it in Apollo's
   web UI (<https://localhost:47990> → PIN).

**Start a game directly (skip Moonlight's menus).** Moonlight TV reads launch parameters
`{"host_uuid": "<host uuid>", "host_app_id": <number>}` (see `src/app/platform/webos/app_webos.c`
in moonlight-tv). Then it connects to that host and starts that app straight away. The host must
be paired, otherwise Moonlight shows its pairing dialog instead.
- Host UUID: `curl http://PC_IP:47989/serverinfo` → `<uniqueid>`.
- App number: in Moonlight, open the PC, highlight the app (e.g. *Steam Big Picture*), open its
  info dialog → `ID: <number>`.
- In `.env`, **in single quotes** (bash would otherwise strip the double quotes):
  `STREAM_APP_PARAMS='{"host_uuid":"A4ED6DC5-…","host_app_id":123456}'`, then `scripts/deploy.sh tv`.

**Finding / checking the app ID** to launch (`STREAM_APP_ID`):
- For apps installed through Developer Mode: `ares-install -d lgtv --list`.
- To test an ID: `ares-launch -d lgtv <id>`. It answers `<not exist>` if the ID is wrong.
- Moonlight TV is `com.limelight.webos`.

**Remote control**: Back exits at any time. On the error screen, Left/Right moves between
Retry and Exit and OK presses. The Magic Remote pointer works too. After the streaming app is
launched, the app closes itself (`TV_CLOSE_AFTER_LAUNCH=1`), so leaving Moonlight does not land
back in it. Pressing the icon again starts over.

**Switching the TV on over the network** (verified: the TV answers about 1 s after the packet):
```sh
scripts/tv-on.sh       # Wake-on-LAN to TV_MAC, waits until the TV answers
```
On the TV this needs *Settings → General → Devices → TV On With Mobile → Turn on via Wi-Fi/LAN*
(the menu name varies by firmware). Use the **wired** MAC if the TV is wired.

**Switching it off**: `scripts/tv-off.sh`. On webOS 26 the Developer Mode SSH user may not call
the power service (`Denied method call "powerOff"`), so this uses LG's control protocol
(wss port 3001) through [aiowebostv](https://github.com/home-assistant-libs/aiowebostv), the
library Home Assistant uses. It is installed into the venv on first use. The first run shows a
pairing prompt on the TV that you accept with the remote; the key is kept in
`STATE_DIR/tv_client_key`. With Quick Start+ the TV keeps answering on the network while it is
off. That is normal, and it is what lets `tv-on.sh` wake it.

## 6. Test with mock mode first

Set `MOCK_PC=1` in `.env` and `scripts/deploy.sh agent`. The agent then pretends there is a
PC: it "boots" `MOCK_BOOT_S` after the wake packet, opens the server `MOCK_STREAM_S` later,
and switches off again after `MOCK_AUTO_OFF_S`. `MOCK_FAIL=boot` or `MOCK_FAIL=stream` shows the
error screens. The TV app shows "MOCK MODE" in the first stage. Set `MOCK_PC=0` and redeploy
for the real PC.

Unit tests (no hardware needed):
```sh
cd agent && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
node tv-app/tests/hmac-test.js
```

## 7. Troubleshooting per stage

| Stage | Message / symptom | Check |
|---|---|---|
| 1 Helper | "offline or unreachable" | Helper on? `curl http://HELPER_HOST:8787/health` from another machine. `journalctl --user -u tvwake-agent`. `HELPER_BIND` must be an address of the helper. |
| any | "rejected this app" / log says `bad signature` | The TV app was built with a different `SHARED_SECRET`: rebuild and reinstall. An `.env` with Windows line endings causes this too (the scripts strip CR now). |
| any | log says `timestamp outside allowed window` | TV clock is wrong. The app corrects it automatically using the helper's time; if it persists, set the TV's time to automatic. |
| 2 | always "off" although the PC is on | Ping blocked and the streaming server not running. Allow ping (section 2) and check Apollo. |
| 3 | "Could not send the wake packet" | `WOL_BROADCAST` must be the LAN's broadcast address (e.g. `192.168.1.255`). |
| 4 | "did not answer within 150 s" | BIOS (ErP off, PCI-E wake on), adapter WoL settings, Fast Startup off, PC wired to the **same** network as the helper (no router in between). Test: `wakeonlan`/`etherwake` from the helper while the PC sleeps. |
| 5 | "nothing answered on port 47989" | Apollo/Sunshine service running? Firewall rules from its installer present? From the helper: `nc -zv PC_IP 47989`. For Steam Link: auto-login, Steam autostart, Remote Play enabled. |
| 6 | "Could not start Moonlight … Is it installed?" | `ares-install -d lgtv --list`; `STREAM_APP_ID` correct? |
| 6 | `Denied method call "launch"` | `requiredACG` must contain `application.launcher` (rebuild). |
| — | Footer "Dev mode: … renewal failing" | `scripts/devmode.sh status`, `journalctl --user -u tvwake-devmode`. Is the TV's key still valid (`ssh -i ~/.ssh/tvwake_tv -p 9922 prisoner@TV_IP`)? |

## Security

- Requests are signed with HMAC-SHA256 over `METHOD\nPATH\nTIMESTAMP\nBODY` using the shared
  secret; the agent rejects timestamps more than 60 s off and replayed signatures.
- The secret is inside the TV app (`config.js` in the IPK). **Anyone who gets the app file can
  read it.** This protects against random devices on your LAN, not against someone who has the
  app file. The worst they can do is wake your PC.
- The agent listens on the LAN address only (`HELPER_BIND`), not on VPN or Docker interfaces.
- The helper stores an unencrypted copy of the TV's dev mode key (`~/.ssh/tvwake_tv`, mode 600)
  and the session token (`STATE_DIR`, mode 600). Anyone with your helper account can use the
  TV's Developer Mode.
- CORS allows any origin because the TV app runs from `file://` (`Origin: null`); the signature,
  not the origin, is what is checked.
