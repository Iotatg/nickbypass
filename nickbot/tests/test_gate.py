"""GT Links gate tests: the CSRF handshake, the countdown and the decoys.

This reproduces the exact failure users hit on vplink.in:

* the gate answers ``POST /links/go`` with 403 "Missing CSRF token cookie"
  unless the request carries a ``csrfToken`` cookie AND a matching
  ``X-CSRF-TOKEN`` header (double submit - any value passes);
* before the countdown runs out it answers ``{"url": "#"}``, not a link;
* the page also carries a "sponsored" anchor pointing at pipefitter.in, which
  is what used to be reported as the real destination.

    python3 tests/test_gate.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nickbot import gates as G  # noqa: E402

PORT = 8813
BASE = f"http://127.0.0.1:{PORT}"
REAL = "https://auth.pwasmultiverse.workers.dev/generate?code=N8BCN7VQDAZFL48O"

PAGE_NO_TIMER = """<!doctype html><html><head><title>Please Wait..</title></head>
<body>
  <p>Please wait while we prepare your link</p>
  <a class="sponsor" href="https://pipefitter.in/article/studyinsurances/">Sponsored</a>
  <script>setTimeout(function(){}, 1000);</script>
</body></html>
"""

PAGE = """<!doctype html><html><head><title>Please Wait..</title></head>
<body>
  <div class="timer" data-seconds="1">1</div>
  <p>Please wait while we prepare your link</p>
  <a class="sponsor" href="https://pipefitter.in/article/studyinsurances/">Sponsored</a>
  <script>
    setTimeout(function(){ /* the real navigation happens after the API call */ }, 1000);
  </script>
</body></html>
"""


FIRST_POST = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8", cookies=()):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/notimer"):
            self._send(200, PAGE_NO_TIMER, cookies=["gt_uc_=notimer; path=/"])
            return
        if self.path.startswith("/nAKfh"):
            self._send(200, PAGE, cookies=[
                "AppSession=deadbeef; path=/; HttpOnly",
                "gt_uc_=nAKfh; path=/",
            ])
            return
        self._send(404, "nope")

    def do_POST(self):
        if not self.path.startswith("/links/go"):
            self._send(404, "nope")
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "ignore"))
        cookie = self.headers.get("Cookie") or ""
        has_cookie = "csrfToken=" in cookie
        header = self.headers.get("X-CSRF-TOKEN") or ""
        if not has_cookie:
            self._send(403, '{"message":"Missing CSRF token cookie","code":403}',
                       "application/json")
            return
        if not header or f"csrfToken={header}" not in cookie.replace(" ", ""):
            self._send(403, '{"message":"CSRF token mismatch.","code":403}',
                       "application/json")
            return
        slug = (form.get("link") or [""])[0]
        started = FIRST_POST.setdefault(slug, time.time())
        if slug not in ("nAKfh", "notimer") or time.time() - started < 1.2:
            self._send(200, '{"status":"success","message":"","url":"#"}', "application/json")
            return
        self._send(200, f'{{"status":"success","message":"","url":"{REAL}"}}', "application/json")

    def log_message(self, *a):
        pass


passed = failed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {extra}")


def _countdown_cases() -> None:
    print("\n[3] countdown parsing")
    check("data-seconds", G._countdown('<div class="timer" data-seconds="15">') == 15)
    check("js variable", G._countdown("var timeleft = 10;") == 10)
    check("no timer", G._countdown("<html>hello</html>") == 0)
    check("capped at 30", G._countdown('data-seconds="999"') == 30)


def _terminal_cases() -> None:
    print("\n[4] destination vs waypoint")
    check("ad host is not a destination", G._is_terminal("https://pipefitter.in/x", "vplink.in") is False)
    check("shortener front page is not a destination", G._is_terminal("https://bit.ly/", "bit.ly") is False)
    check("short code is not a destination", G._is_terminal("https://bit.ly/abc123", "bit.ly") is False)
    check("deep path on another host is a destination",
          G._is_terminal("https://example.org/blog/post-1", "bit.ly") is True)
    check("deep path on the same host is a destination",
          G._is_terminal(f"{BASE}/generate?code=x", "127.0.0.1") is True)


async def main() -> int:
    global STARTED
    STARTED = time.time()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    r = G.FastResolver(timeout=8)
    try:
        print("\n[1] the CSRF handshake is what unlocks /links/go")
        c = await r._isolated_client()
        try:
            html = (await c.get(f"{BASE}/nAKfh")).text
            # without the pair the gate refuses
            resp = await c.post(f"{BASE}/links/go", data={"link": "nAKfh"})
            check("no csrf -> 403", resp.status_code == 403, str(resp.status_code))
            check("the gate asks for a CSRF token", "CSRF token" in resp.text, resp.text[:80])

            # with the pair the resolver mints, it answers
            target, _msg, err = await r._gate_json(c, f"{BASE}/nAKfh", html)
            check("no error once the pair is sent", err == "", err)
            check("waited out the countdown and got the link", target == REAL, target or "empty")
        finally:
            await c.aclose()

        print("\n[2] a real resolution never returns the sponsored decoy")
        # Force the GT code path for this local host.
        original = G._is_gt
        G._is_gt = lambda host: True
        try:
            d = await r.resolve(f"{BASE}/nAKfh")
        finally:
            G._is_gt = original
        check("resolved", d["ok"], d.get("note", ""))
        check("destination is the real one", d["final"] == REAL, d["final"])
        check("not the pipefitter decoy", "pipefitter" not in d["final"], d["final"])
        check("method is the site API", d["method"] == "gate", d["method"])

        print("\n[2b] no timer in the HTML: retry until the gate is ready")
        original = G._is_gt
        G._is_gt = lambda host: True
        try:
            d2 = await r.resolve(f"{BASE}/notimer")
        finally:
            G._is_gt = original
        check("resolved without a visible countdown", d2["ok"], d2.get("note", ""))
        check("same real destination", d2["final"] == REAL, d2["final"])
    finally:
        await r.aclose()
        srv.shutdown()

    _countdown_cases()
    _terminal_cases()

    print(f"\nResult: {passed} passed / {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
