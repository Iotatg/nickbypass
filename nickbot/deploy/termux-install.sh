#!/data/data/com.termux/files/usr/bin/bash
# Nick Bypass — one-shot Termux installer.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/Iotatg/nickbypass/main/nickbot/deploy/termux-install.sh)
#
# What it does, in order:
#   1. installs python + git + tmux
#   2. clones the repo (or updates it, if you run it from one)
#   3. asks for BOT_TOKEN / OWNER_ID once and writes .env
#   4. checks the config, starts the bot in tmux
#   5. installs the Termux:Boot hook so it comes back after a reboot
#
# Afterwards you never need this file again: /diag, /restart, /update and
# /logs in the chat do everything else.
set -u

REPO_URL="${REPO_URL:-https://github.com/Iotatg/nickbypass.git}"
REPO_DIR="${NICKBOT_REPO:-$HOME/nickbypass}"     # the git checkout
DIR="$REPO_DIR/nickbot"                          # the folder holding nickbot/
BOOT_DIR="$HOME/.termux/boot"

say()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

# ---------------------------------------------------------------- 0. sanity
[ -n "${PREFIX:-}" ] && [ -d "$PREFIX" ] || \
  die "This is the Termux installer. Run it inside the Termux app."
command -v pkg >/dev/null || die "pkg not found — is this really Termux?"

say "1/6 · Packages"
pkg update -y >/dev/null 2>&1 || warn "pkg update had warnings, carrying on"
pkg install -y python git tmux termux-tools || die "pkg install failed"
ok "python $(python -c 'import platform;print(platform.python_version())')"

termux-wake-lock 2>/dev/null || warn "wake-lock unavailable (battery may pause the bot)"

# ---------------------------------------------------------------- 1. code
say "2/6 · Code"
if [ -f "$PWD/nickbot/__main__.py" ]; then
  DIR="$PWD"
  REPO_DIR="$(dirname "$DIR")"
  ok "using this checkout: $DIR"
elif [ -d "$REPO_DIR/.git" ]; then
  ok "using existing checkout: $REPO_DIR"
  git -C "$REPO_DIR" pull --ff-only || warn "could not pull, keeping the local copy"
else
  rm -rf "$REPO_DIR"
  git clone --depth 1 "$REPO_URL" "$REPO_DIR" || die "git clone failed — check the URL / your connection"
  ok "cloned into $REPO_DIR"
fi
cd "$DIR" || die "cannot enter $DIR"

# ---------------------------------------------------------------- 2. deps
say "3/6 · Python packages"
python -m pip install --upgrade pip >/dev/null 2>&1 || true
python -m pip install -r requirements-termux.txt || die "pip install failed"
ok "dependencies ready"

# ---------------------------------------------------------------- 3. config
say "4/6 · Config"
if [ -f .env ]; then
  ok ".env already exists — leaving it alone"
else
  cp .env.example .env

  printf '\nBot token from @BotFather (looks like 8123456789:AAH...): '
  read -r TOKEN
  TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
  printf '%s' "$TOKEN" | grep -Eq '^[0-9]{6,}:[A-Za-z0-9_-]{30,}$' || \
    die "That does not look like a bot token. Get one from @BotFather and run me again."

  printf 'Your numeric Telegram ID (send /id to @userinfobot): '
  read -r OWNER
  OWNER="$(printf '%s' "$OWNER" | tr -d '[:space:]')"
  printf '%s' "$OWNER" | grep -Eq '^-?[0-9]{5,}$' || \
    die "OWNER_ID must be a number (yours, not the bot's)."

  # Rewrite only the two keys, so the comments in .env.example survive.
  python - "$TOKEN" "$OWNER" <<'PY'
import re, sys
token, owner = sys.argv[1], sys.argv[2]
text = open(".env").read()
for key, val in (("BOT_TOKEN", token), ("OWNER_ID", owner)):
    if re.search(rf"(?m)^{key}=.*$", text):
        text = re.sub(rf"(?m)^{key}=.*$", f"{key}={val}", text)
    else:
        text += f"\n{key}={val}\n"
open(".env", "w").write(text)
PY
  chmod 600 .env
  ok ".env written (mode 600)"

  printf '\nOptional: Kurigram (MTProto) is faster than the Bot API.\n'
  printf 'Press Enter to skip, or paste API_ID (from my.telegram.org): '
  read -r API_ID
  if [ -n "${API_ID:-}" ]; then
    printf 'API_HASH: '
    read -r API_HASH
    python - "$API_ID" "$API_HASH" <<'PY'
import re, sys
api_id, api_hash = sys.argv[1].strip(), sys.argv[2].strip()
text = open(".env").read()
for key, val in (("API_ID", api_id), ("API_HASH", api_hash)):
    if re.search(rf"(?m)^{key}=.*$", text):
        text = re.sub(rf"(?m)^{key}=.*$", f"{key}={val}", text)
    else:
        text += f"\n{key}={val}\n"
open(".env", "w").write(text)
PY
    ok "MTProto credentials stored"
  else
    ok "staying on the Bot API backend"
  fi
fi

# ---------------------------------------------------------------- 4. check
say "5/6 · Config check"
python -m nickbot --check || die "The check failed — fix .env and run me again."
ok "configuration is valid"

# ---------------------------------------------------------------- 5. boot
say "6/6 · Autostart + launch"
mkdir -p "$BOOT_DIR"
cat > "$BOOT_DIR/nickbot.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
# Installed by deploy/termux-install.sh — starts the bot when Android boots.
termux-wake-lock 2>/dev/null
sleep 8
cd "$DIR" || exit 0
[ -f data/stopped ] && exit 0
command -v tmux >/dev/null || exit 0
tmux has-session -t nickbot 2>/dev/null && exit 0
tmux new-session -d -s nickbot "bash deploy/termux-supervise.sh"
EOF
chmod +x "$BOOT_DIR/nickbot.sh"
ok "boot hook: $BOOT_DIR/nickbot.sh"

if [ -d "$HOME/.termux" ]; then
  warn "install the Termux:Boot app (F-Droid) once, or autostart will not fire"
fi

rm -f data/stopped
bash deploy/termux-run.sh start || die "the bot did not start — see the log above"

cat <<EOF

────────────────────────────────────────────────────────────
 Done. From now on you do not need this terminal.

   Telegram chat        what it does
   /diag                machine, uptime, RAM, battery, autostart
   /restart             restart without touching Termux
   /update              git pull + restart (one command)
   /logs 40             last 40 log lines
   /status              engine health

   If you ever do come back:
     cd $DIR && bash deploy/termux-run.sh start|stop|log

 Keep Termux out of battery optimisation, otherwise Android
 freezes the bot when the screen is off.
────────────────────────────────────────────────────────────
EOF
