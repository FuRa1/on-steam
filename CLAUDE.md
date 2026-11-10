# CLAUDE.md

Hobby project: press "PC Game" on an LG TV → the helper (ThinkPad L590, Ubuntu) wakes the gaming PC
(Windows, Apollo) → the TV launches Moonlight. Read `README.md` (setup, design) and
**`docs/OPERATIONS.md`** (runbook, device facts verified by hand, open items) before changing anything.

## Machines and access

- **Workstation = the gaming PC** (`MoralMe`, Windows 11) or the **MacBook**; the repo is checked out on both.
  On the PC: **no Python**; use Git Bash for scripts, PowerShell for Windows checks. Node is available.
- **From the MacBook**: `ssh moralme` (the PC, admin PowerShell: pipe scripts on stdin, see OPERATIONS §3),
  `ssh l590`, `ssh lgtv` (TV directly) / `ssh lgtv-remote` (via the helper). The Mac checkout keeps the
  config as untracked `env` (never commit it).
- **Helper**: `ssh l590` (passwordless, user `akeya`, **sudo needs a password**, so you can't use it).
  Everything runs as systemd **user** units there; no root needed.
- **TV**: reachable from the helper only: `ssh -tt -i ~/.ssh/tvwake_tv -p 9922 -o PubkeyAcceptedAlgorithms=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa prisoner@192.168.1.111`.
  webOS CLI on the helper: `export PATH=$HOME/.local/bin:$PATH; ares-* -d lgtv`.
- If this PC sleeps or shuts down, your session stops; the helper's journal keeps the logs.

## Workflow

- Edit here → `bash scripts/deploy.sh agent` (and/or `tv`): copies the repo and `.env` to the helper
  and reinstalls. For quick checks: `tar ... | ssh l590 'tar -x -C ~/tv-wake-launcher'`.
- Tests run **on the helper**: `ssh l590 'cd ~/tv-wake-launcher/agent && .venv/bin/python -m pytest -q'`
  (82 tests, all must pass). JS HMAC: `node tv-app/tests/hmac-test.js` (runs here).
- Logs: `ssh l590 'journalctl --user -u tvwake-agent -n 50 -o cat'` (`tvwake-devmode` for renewal).
- Test the wake flow without the TV: `scripts/agent-cli.sh wake` on the helper.
- Apollo log on this PC: `C:\Program Files\Apollo\config\sunshine.log` (resolution, bitrate, codec per session).

## Rules

- **Never commit `.env`** or any secret (`SHARED_SECRET`, the TV key passphrase, client keys).
  Before committing, grep the files for the secret value from `.env`.
- **Commits: no Claude / AI co-author or "Generated with" lines.** The user asked for this.
- Everything device-specific goes in `.env` (+ a commented entry in `.env.example`); no IPs/MACs in code.
  JSON values in `.env` need single quotes (bash strips double quotes).
- Line endings must stay LF (`.gitattributes`). A CRLF `.env` once broke HMAC auth (the TV got a secret
  with `\r`); `scripts/lib.sh` strips CR, so keep loading `.env` through `load_env`.
- Installing third-party code onto the TV (e.g. Moonlight IPK) needs the user's explicit OK; verify
  sha256 against the release manifest.
- Don't power-cycle or wake the user's devices just to test: ask first. Turning the TV on or off was
  done on request.
- The user writes Russian or English (often by voice, with typos); answer in the language of their message.

## Things that bite (verified on webOS 26 / this hardware)

- `luna-send-pub` prints nothing without a TTY → always `ssh -tt`. `getForegroundAppInfo`, `listApps`,
  and `tvpower/*` are **denied** for `prisoner`; app launch and `config/getConfigs` work.
  Power off goes via SSAP (`scripts/tv-off.sh`, aiowebostv).
- The TV app needs `"requiredACG": ["application.launcher"]` to launch Moonlight.
- WebCrypto works in the `file://` app; the JS SHA-256 is only a fallback.
- The dev mode session is 1000 h; the extend via `com.palmdts.devmode {"extend":true}` works over SSH.
- The PC must be on the main LAN (192.168.1.x). The ER605 at .232 is a NAT router: WoL can't cross it.
- The ping binary on the helper uses file capabilities, so don't add `NoNewPrivileges=` to the units.
- There's no Steam Link for webOS; the backend is Moonlight + Apollo (`STREAM_BACKEND=moonlight`, port 47989).

## Code map

`agent/tvwake/`: `config.py` (.env parser + validation), `auth.py` (HMAC + replay cache), `wake.py`
(state machine, `Clock` injected; tests use `FakeClock`), `probes.py`/`wol.py`/`mock.py`, `api.py`
(FastAPI, CORS for `Origin: null`), `devmode.py` (timer tick), `tvpower.py`, `client.py`.
`notify.py` (Telegram, optional), `ipwatch.py` (public IP check every 5 h → Telegram on change, timer
`tvwake-ipwatch`, OPERATIONS §11), `power.py` (`POST /shutdown` for the "PC Off" app: ssh `PC_SSH`
`shutdown /s /f`). `tv-off-app/` = the "PC Off" icon (shares js/css with `tv-app/`; built by
`scripts/tv-off-app.sh`, deploy target `tv-off`). `pc/events.ps1` (+ `install-events.ps1`): Apollo hooks + power events →
Telegram; auto-sleep exists but is OFF since 2026-11-10 (`AUTOSLEEP=off`, OPERATIONS §9).
`tv-app/js/app.js` is the UI controller (generation counter cancels stale flows; global error handlers).
