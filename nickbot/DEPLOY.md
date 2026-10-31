# Deploying Nick Bypass

Every way to run this thing, from a ₹0 Android phone to a $4 VPS. Pick the row
that matches what you have.

| Where you're running it | Fast HTTP path | Browser fallback | Notes |
|---|---|---|---|
| **Termux** (Android phone) | ✅ | ❌ not possible on Android | Your phone's IP is residential — the best possible exit for IP-walled gates |
| **Ubuntu / Debian VPS** | ✅ | ✅ | 24/7, but a datacenter IP needs `PROXY` for `vplink` and friends |
| **Windows** (native or WSL2) | ✅ | ✅ | Fine for a desktop that stays on |
| **macOS** | ✅ | ✅ | Same as above |
| **Docker** | ✅ | ✅ (opt-in build) | Best for servers; polling needs no open port |
| **Raspberry Pi / ARM SBC** | ✅ | ⚠️ usually | arm64 Chromium exists, but it's heavy on a 1 GB board |
| **PaaS** (Railway, Render, Fly, Koyeb) | ✅ | ⚠️ big image | Free tiers sleep; data dir is ephemeral |

The bot uses **long polling**, so it never needs an inbound port, a domain or TLS.
If it can reach `api.telegram.org`, it runs.

---

## 0. Before anything

**a) Get a bot token.** Open [@BotFather](https://t.me/BotFather) → `/newbot` → name → username → copy the `123456:AA…` string.

**b) Get your numeric ID.** Message [@userinfobot](https://t.me/userinfobot), or start the bot later and send `/id`.

**c) Never paste the token anywhere public.** If it leaks, `/revoke` in BotFather immediately and put the new one in `.env`.

**d) Optional: Kurigram credentials.** The bot can run on MTProto (Kurigram)
instead of the HTTP Bot API. It needs an app pair on top of the bot token:

1. Open <https://my.telegram.org> and log in with your own number.
2. *API development tools* → create an app (any name, platform "Desktop").
3. Copy `api_id` (a number) and `api_hash` (32 hex characters).

Kurigram is optional: leave the fields empty and the bot uses
python-telegram-bot, which only needs the token.

Your `.env` needs at minimum:

```ini
BOT_TOKEN=123456:AAH...
OWNER_ID=123456789
```

Add these two lines only if you want Kurigram:

```ini
BACKEND=kurigram
API_ID=1234567
API_HASH=0123456789abcdef0123456789abcdef
```

Everything else has a sane default. Full list with explanations is in
[`.env.example`](.env.example).

Verify any install with:

```bash
python -m nickbot --check
```

```
Config check
  token      : set (123456…hDfY)
  api id     : missing (Kurigram only)
  api hash   : missing (Kurigram only)
  backend    : auto -> ptb
  owner      : [123456789]
  problems   : none

Environment
  python     : 3.12.4 · aarch64 · Linux
  backend    : python-telegram-bot
  http/2     : on (h2 installed)
  browser    : not installed - HTTP path only
  allow_browser=false · proxy_farm=false (IN) · proxy=none
```

That `Environment` block is the honest answer to "will this box work".

---

## 1. Termux (Android)

Works on any phone from ~Android 7, no root, no PC. This is also the setup most
likely to resolve `vplink` links, because your phone sits on a residential
mobile/wifi IP.

### 1.1 Install the right Termux

**Do not use the Play Store build** — it is abandoned and breaks package installs.
Use F-Droid:

- [f-droid.org/packages/com.termux](https://f-droid.org/packages/com.termux/)
- or the GitHub releases page for `termux-app`

Optional but recommended add-ons from the same source:

| Add-on | Why |
|---|---|
| **Termux:Boot** | starts the bot when the phone boots |
| **Termux:API** | not required, useful for notifications |

### 1.2 Base packages

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git tmux termux-tools
```

### 1.3 Keep Android from killing it

```bash
termux-wake-lock
```

Then, in Android settings → Apps → Termux → Battery → **Unrestricted**
(Xiaomi/Oppo/Vivo also need "Autostart" enabled, and Realme needs "Allow
background activity"). Without this, Android freezes the process after a few
minutes of screen-off and the bot goes silent.

### 1.4 Clone and install

```bash
cd ~
git clone https://github.com/Iotatg/nickbypass.git
cd nickbypass/nickbot
pip install -r requirements-termux.txt
```

`requirements-termux.txt` skips Playwright on purpose: Playwright ships
glibc-linked Chromium and Android uses bionic, so there is nothing to install and
nothing to fix. The HTTP + site-API path resolves most gates anyway.

Keep `PROXY_FARM=false` on a phone. The pool only wakes up after a site actually
walls your IP, and it scans gently (12 at a time), but on mobile data it is still
pure waste — your own IP is the residential one these sites want.

### 1.5 Configure

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

```ini
BOT_TOKEN=123456:AAH...
OWNER_ID=123456789
ALLOW_BROWSER=false
PROXY=
PROXY_FARM=false
```

Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

Then:

```bash
python -m nickbot --check
```

### 1.6 Run it

```bash
bash deploy/termux-run.sh
```

It starts the bot inside a `tmux` session, so closing Termux or switching apps
doesn't kill it. Handy commands:

```bash
bash deploy/termux-run.sh log      # follow the log
bash deploy/termux-run.sh stop     # stop it
tmux attach -t nickbot             # look inside the session (Ctrl+B then D to leave)
```

Now send your bot a link in Telegram. If it answers, you're done.

### 1.7 Start automatically on boot

Install **Termux:Boot** from F-Droid, open it once, then:

```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/nickbot.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd $HOME/nickbypass/nickbot
tmux new-session -d -s nickbot "python -m nickbot >> data/bot.log 2>&1"
EOF
chmod +x ~/.termux/boot/nickbot.sh
```

### 1.8 Do you need a proxy on a phone?

No. Check with `/ip` in the bot:

```
Direct — the machine running the bot
49.36.12.84 · IN · Mumbai
AS55836 · Reliance Jio Infocomm Limited
🏠 home or mobile line

Verdict
You're on an Indian home line, so vplink, gplinks and friends open without any
proxy.
```

If it says `🏢 datacenter / hosting`, you're on a VPN or a weird ISP — turn the
VPN off, or set `PROXY`.

### 1.9 Termux gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Dies after a few minutes | Android battery optimisation | `termux-wake-lock` + Unrestricted battery |
| `pip install` fails on `playwright` | expected | use `requirements-termux.txt` |
| `pkg update` errors about mirrors | old Play Store build | reinstall from F-Droid |
| Bot replies twice | two instances running | `bash deploy/termux-run.sh stop`, then check `ps aux \| grep nickbot` |
| Can't write to `~/storage` | not set up | keep the repo in `$HOME`, or run `termux-setup-storage` |

### 1.10 Running it on a phone but reading it from a PC

```bash
pkg install -y openssh
passwd            # set a password
sshd              # starts on port 8022
whoami            # your username
```

From the PC: `ssh -p 8022 user@PHONE_IP`. Phone IP: `ifconfig` or Settings → WiFi.

---

## 2. Ubuntu / Debian VPS

For 24/7 uptime. Any 1 GB box works (Hetzner, DigitalOcean, Vultr, Contabo,
Oracle free tier).

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git

sudo adduser --system --group --home /opt/nickbot nickbot
sudo -u nickbot git clone https://github.com/Iotatg/nickbypass.git /opt/nickbot
cd /opt/nickbot/nickbot

sudo -u nickbot python3 -m venv .venv
sudo -u nickbot .venv/bin/pip install -r requirements.txt
sudo .venv/bin/playwright install-deps chromium     # system libs, needs root
sudo -u nickbot .venv/bin/playwright install chromium
```

The split matters: `--with-deps` runs `apt-get`, so it has to be root, while the
browser download must land in the `nickbot` user's cache.

Config:

```bash
sudo -u nickbot cp .env.example .env
sudo -u nickbot nano .env
sudo chmod 600 .env
sudo chown nickbot:nickbot .env
```

Test before installing the service:

```bash
sudo -u nickbot .venv/bin/python -m nickbot --check
sudo -u nickbot .venv/bin/python -m nickbot --self-test "https://gplinks.co/test"
```

Then systemd:

```bash
sudo cp deploy/nickbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nickbot
sudo systemctl status nickbot --no-pager
journalctl -u nickbot -f          # live log
```

### A VPS is a datacenter IP

That is the one thing a VPS cannot fix. `/ip` will say `🏢 datacenter / hosting`,
and `vplink`-style gates will refuse it no matter which browser you run. Options,
best first:

1. `PROXY=http://user:pass@residential-host:port` — a residential/mobile proxy.
2. `PROXY_FARM=true` + `/proxy on IN` — free pool, opportunistic (see README).
3. Run the bot on your phone instead (section 1) and keep the VPS for something else.

---

## 3. Windows

### 3.1 Native

1. Install Python 3.11+ from [python.org](https://www.python.org/downloads/) — tick **Add python.exe to PATH**.
2. Open PowerShell:

```powershell
git clone https://github.com/Iotatg/nickbypass.git
cd nickbypass\nickbot
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
notepad .env
python -m nickbot --check
python -m nickbot
```

If PowerShell blocks activation: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**Start with Windows:** Task Scheduler → Create Task → Trigger "At log on" →
Action → Program: `C:\path\nickbypass\nickbot\.venv\Scripts\python.exe`,
Arguments: `-m nickbot`, Start in: `C:\path\nickbypass\nickbot`.
Tick "Run whether user is logged on or not" if you want it headless.

### 3.2 WSL2 (recommended on Windows)

```powershell
wsl --install -d Ubuntu
```

Then follow **section 2** inside Ubuntu, but keep the repo in `~` rather than
`/mnt/c` — file access across the Windows boundary is slow and breaks some
`venv` installs.

---

## 4. macOS

```bash
brew install python@3.12 git
git clone https://github.com/Iotatg/nickbypass.git
cd nickbypass/nickbot
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env && nano .env
python -m nickbot --check
```

Keep it running after the lid closes — `caffeinate` helps only while plugged in;
for a real always-on setup use a LaunchAgent:

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.nickbot.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.nickbot</string>
  <key>ProgramArguments</key><array>
    <string>$HOME/nickbypass/nickbot/.venv/bin/python</string>
    <string>-m</string><string>nickbot</string>
  </array>
  <key>WorkingDirectory</key><string>$HOME/nickbypass/nickbot</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/nickbypass/nickbot/data/bot.log</string>
  <key>StandardErrorPath</key><string>$HOME/nickbypass/nickbot/data/bot.log</string>
</dict></plist>
EOF
launchctl load ~/Library/LaunchAgents/com.nickbot.plist
```

---

## 5. Docker

From the repo root:

```bash
cp nickbot/.env.example nickbot/.env
nano nickbot/.env          # BOT_TOKEN + OWNER_ID

docker build -t nickbot .
docker run -d --name nickbot \
  --env-file nickbot/.env \
  -v nickdata:/app/nickbot/data \
  --restart unless-stopped \
  nickbot

docker logs -f nickbot
```

With Compose:

```bash
docker compose up -d --build
docker compose logs -f
```

Want Chromium inside the image (over 1 GB instead of ~280 MB)?

```bash
docker build --build-arg WITH_BROWSER=1 -t nickbot:browser .
```

then run with `-e ALLOW_BROWSER=true`. Without it the container resolves through
HTTP only, which is fine for most links and much lighter.

The `nickdata` volume holds `nickbot.db` — users, bans, request log. Drop the
volume and you lose `/stats`.

---

## 6. Raspberry Pi / ARM boards

Same as section 2, with two notes:

- Use a 64-bit OS (`arm64`). 32-bit `armhf` has no Chromium build for Playwright.
- On a 1 GB board, run with `ALLOW_BROWSER=false` and `BROWSER_CONCURRENCY=1`;
  a headless Chromium page can eat 300 MB.

A Pi at home is a good deal: always on **and** a residential IP, so no proxy
needed for the IP-gated sites.

---

## 7. PaaS (Railway / Render / Fly.io / Koyeb)

These platforms expect a web service, but this bot only polls — deploy it as a
**worker / background process**, not a web service, and don't set a port.

| Platform | How |
|---|---|
| **Railway** | New project → Deploy from GitHub → Settings → *Remove* the public domain → Variables: `BOT_TOKEN`, `OWNER_ID` |
| **Render** | New → Background Worker → repo → build `pip install -r nickbot/requirements.txt` → start `cd nickbot && python -m nickbot` |
| **Fly.io** | `fly launch` (say no to a public service), `fly secrets set BOT_TOKEN=… OWNER_ID=…` |
| **Koyeb** | Worker type, run command as above |

Caveats worth knowing before you try:

- **Free tiers sleep** after inactivity. A sleeping bot doesn't answer, and
  Telegram keeps the updates queued — it will dump them all when it wakes.
- **No persistent disk** on free tiers: `nickbot.db` resets on every deploy.
- **Datacenter IP** — same wall as a VPS. Set `PROXY`, or expect `vplink` to fail.
- Images with Chromium usually exceed free build limits. Use `ALLOW_BROWSER=false`.

---

## 8. Any bare terminal (no systemd, no Docker)

If you just want it running in a shell you can close:

```bash
tmux new -s nickbot
python -m nickbot 2>&1 | tee -a data/bot.log
# detach: Ctrl+B then D      reattach: tmux attach -t nickbot
```

or:

```bash
nohup python -m nickbot >> data/bot.log 2>&1 &
echo $! > data/bot.pid
kill "$(cat data/bot.pid)"     # to stop
```

Works everywhere: shared hosting shells, Android, containers, CI boxes.

---

## 9. After it's running

| Task | Command |
|---|---|
| Health | `python -m nickbot --check` |
| Resolve without Telegram | `python -m nickbot --self-test "https://gplinks.co/x"` |
| Live log | `journalctl -u nickbot -f` / `docker logs -f nickbot` / `tail -f data/bot.log` |
| Update | `git pull && pip install -r requirements.txt && <restart>` |
| Backup | copy `data/nickbot.db` |
| Wipe stats | delete `data/nickbot.db` while stopped |

Set the command menu (once, after the first start): the bot does it itself in
`_post_init`, so `/start` looks right without touching BotFather.

---

## 10. Troubleshooting

| Error / symptom | Meaning | Fix |
|---|---|---|
| `Conflict: terminated by other getUpdates request` | two copies polling the same token | kill the extra one: `ps aux \| grep nickbot` |
| `InvalidToken` / `401 Unauthorized` | wrong or revoked token | new token from BotFather |
| `Startup failed: BOT_TOKEN missing` | `.env` not found or not filled | check you're in `nickbot/`, `cat .env` |
| Bot silent in groups | privacy mode on | BotFather → `/setprivacy` → Disable, then re-add to the group |
| `sqlite3.OperationalError: database is locked` | two instances, or a stale lock | stop all, delete `data/*.db-journal` |
| `Browser engine unavailable here` | Playwright/Chromium missing | `playwright install chromium`, or accept HTTP-only |
| `Playwright` install fails on Termux | expected — Android | use `requirements-termux.txt`, `ALLOW_BROWSER=false` |
| Everything times out | DNS or firewall | `curl -s https://api.telegram.org` should return JSON |
| `403` from `vplink` | datacenter ASN | `/ip` to confirm, then `PROXY` |
| Bot answers, then stops after minutes | Android/iOS killed it | wake-lock + battery unrestricted (section 1.3) |
| `Timed out` on every link | clock skew > 5 min | sync time: `sudo timedatectl set-ntp true` |

---

## 11. Security

- `.env` is `chmod 600` and git-ignored. Keep it that way.
- Run as a non-root user (`nickbot`, not `root`). The systemd unit in `deploy/`
  already assumes that and locks the filesystem down.
- The bot only needs outbound HTTPS. If you use a firewall, allow egress and
  block everything inbound — polling needs no ports.
- One token, one running instance. Copying the same token to two machines gives
  you the `Conflict` error and random dropped updates.
- Leaked token → BotFather `/revoke` → new token → update `.env` → restart.
  Everything else (owner ID, users) stays.
