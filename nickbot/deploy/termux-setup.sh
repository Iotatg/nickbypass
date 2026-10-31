#!/data/data/com.termux/files/usr/bin/bash
# Kept for muscle memory: this used to be the setup script.
# It now just runs the full installer, which also handles cloning, .env,
# the config check, autostart and the first launch.
#
#   bash deploy/termux-setup.sh
set -eu
cd "$(dirname "$0")/.."
exec bash deploy/termux-install.sh "$@"
