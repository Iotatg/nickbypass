#!/usr/bin/env bash
# 一键复现：离线单测 + 真实短链 + 本地广告门（HTTP / 浏览器两条路径）
set -u
cd "$(dirname "$0")"

echo "== 1. 离线单测 =="
python3 tests/test_offline.py || exit 1

echo
echo "== 2. 真实短链（需要联网）=="
python3 -m linkresolver https://tinyurl.com/yjqg3sug https://is.gd/q7x9eD https://v.gd/CCGtUT --timeout 15

echo
echo "== 3. 本地广告门：纯 HTTP 应判为需要浏览器 =="
(cd tests/fixtures && python3 -m http.server 8765 >/dev/null 2>&1 &) ; sleep 1
python3 -m linkresolver http://127.0.0.1:8765/gate2.html --no-browser --timeout 12

echo
echo "== 4. 本地广告门：浏览器兜底 =="
python3 -m linkresolver http://127.0.0.1:8765/gate2.html --timeout 25
