#!/data/data/com.termux/files/usr/bin/bash
# Install (or remove) the Termux:Boot hook, so the bot starts by itself after
# Android reboots the phone.
#
#   bash deploy/termux-boot.sh          install
#   bash deploy/termux-boot.sh remove   uninstall
#
# Needs the Termux:Boot app once (F-Droid). Without it, nothing here runs at
# boot — the hook file is harmless either way.
set -eu
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
HOOK="$HOME/.termux/boot/nickbot.sh"

case "${1:-install}" in
  remove)
    rm -f "$HOOK"
    echo "removed $HOOK"
    exit 0
    ;;
  install) ;;
  *) echo "usage: bash deploy/termux-boot.sh [install|remove]"; exit 1 ;;
esac

[ -n "${PREFIX:-}" ] || { echo "This only makes sense inside Termux."; exit 1; }
command -v tmux >/dev/null || { echo "Install tmux first: pkg install tmux"; exit 1; }

mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
# Written by deploy/termux-boot.sh — do not edit by hand.
termux-wake-lock 2>/dev/null
sleep 8
cd "$ROOT" || exit 0
[ -f data/stopped ] && exit 0
command -v tmux >/dev/null || exit 0
tmux has-session -t nickbot 2>/dev/null && exit 0
tmux new-session -d -s nickbot "python -m nickbot >> data/bot.log 2>&1"
EOF
chmod +x "$HOOK"

echo "installed: $HOOK"
echo "boot folder: $(dirname "$HOOK")"
echo
echo "Install the Termux:Boot app once, open it, and allow it to run at boot."
echo "Then reboot the phone to confirm — /diag in the chat shows 'Autostart: on'."
