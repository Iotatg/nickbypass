#!/usr/bin/env bash
# One-shot reproduction: offline tests + real short links + local gate (HTTP and browser paths)
set -u
cd "$(dirname "$0")"

echo "== 1. offline tests =="
python3 tests/test_offline.py || exit 1

echo
echo "== 2. real short links (needs network) =="
python3 -m linkresolver https://tinyurl.com/yjqg3sug https://is.gd/q7x9eD https://v.gd/CCGtUT --timeout 15

echo
echo "== 3. local gate: HTTP only should say a browser is needed =="
(cd tests/fixtures && python3 -m http.server 8765 >/dev/null 2>&1 &) ; sleep 1
python3 -m linkresolver http://127.0.0.1:8765/gate2.html --no-browser --timeout 12

echo
echo "== 4. local gate: browser fallback =="
python3 -m linkresolver http://127.0.0.1:8765/gate2.html --timeout 25
