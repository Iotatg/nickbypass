#!/data/data/com.termux/files/usr/bin/bash
# One-shot Termux setup for the Nick Bypass bot.
#   bash deploy/termux-setup.sh
# Run it from the repo root (the folder that contains nickbot/).
set -e

say() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

[ -d nickbot ] || die "Run this from the repo root (no nickbot/ folder here)."

say "Packages"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python git tmux termux-tools

say "Keep the CPU awake while we work"
termux-wake-lock 2>/dev/null || true

say "Python deps (no playwright on Android)"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements-termux.txt

say "Config"
if [ ! -f .env ]; then
  cp .env.example .env
  printf '\n.env created. Fill these two, then run again:\n'
  printf '  BOT_TOKEN=123456:AA...      (from @BotFather)\n'
  printf '  OWNER_ID=123456789          (from /id, or @userinfobot)\n\n'
  printf 'Edit with:  nano .env\n'
  exit 0
fi

chmod 600 .env

say "Sanity check"
python -m nickbot --check || die "Config check failed — fix .env and rerun."

say "Done. Start it with:"
printf '  bash deploy/termux-run.sh\n'
