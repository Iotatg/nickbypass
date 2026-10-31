"""The bug the step-trail rule fixes, reproduced on a real browser.

A gate chain does not stop at the auth endpoint in the browser - the auth page
navigates on by itself, so ``BrowserResolver.resolve()`` returns the ad page
behind it:

    gate (127.0.0.1)  ->  auth endpoint (localhost)  ->  ad page (localhost)

Chromium really does visit the auth URL; it is only the *last* URL that is
wrong. So the resolver has to read the navigation trail, and this test proves
both halves against a real Chromium:

* ``BrowserResolver`` returns the ad page, with the auth URL in its steps
  (the behaviour that produced "pipefitter.in/..." in the chat);
* ``Engine._one_hop`` returns the auth URL, because it reads that trail.

Needs Playwright + Chromium; prints "skipped" and passes when they are missing
(the phone runs Selenium, where this check cannot run either).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nickbot import hops                    # noqa: E402
from nickbot.config import Config           # noqa: E402
from nickbot.resolver import Engine         # noqa: E402

PORT = 8831
GATE = f"http://127.0.0.1:{PORT}/nAKfh"
AUTH = f"http://localhost:{PORT}/generate?code=N8BCN7VQDAZFL48O"
AD = f"http://localhost:{PORT}/pipefitter/studyinsurances/best-cancer-treatment/"

GATE_PAGE = """<!doctype html><html><head><title>Please Wait..</title></head>
<body><p>Please wait while we prepare your link</p>
<button id="go">Continue</button>
<script>
  var left = 2, t = setInterval(function(){
    left--;
    document.getElementById('go').textContent = 'Continue (' + left + ')';
    if (left <= 0) { clearInterval(t);
      setTimeout(function(){ location.href = '%s'; }, 400); }
  }, 500);
</script></body></html>
""" % AUTH

AUTH_PAGE = """<!doctype html><html><head><title>Your Auth Key</title></head>
<body><h1>Your Auth Key</h1><p>N8BCN7VQDAZFL48O</p>
<a href="%s">Continue</a>
<script>setTimeout(function(){ location.href = '%s'; }, 700);</script>
</body></html>
""" % (AD, AD)

AD_PAGE = """<!doctype html><html><head><title>Best cancer treatment hospitals</title></head>
<body><h1>Best cancer treatment hospitals in India 2026</h1></body></html>
"""

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


class Handler(BaseHTTPRequestHandler):
    def _send(self, body):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/nAKfh"):
            return self._send(GATE_PAGE)
        if self.path.startswith("/generate"):
            return self._send(AUTH_PAGE)
        if self.path.startswith("/pipefitter"):
            return self._send(AD_PAGE)
        self.send_error(404)

    def log_message(self, *a):
        pass


def main() -> int:
    from linkresolver.browser import BrowserResolver

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    try:
        print("\n[1] what the browser engine returns on its own")
        final, steps, note = BrowserResolver(headless=True, timeout=20).resolve(GATE)
        if "not installed" in note.lower() or "failed to start" in note.lower():
            print(f"  - skipped: {note}")
            return 0
        trail = [getattr(s, "url", "") for s in steps]
        check("it ends on the ad page", "pipefitter" in final, final)
        check("...because the auth page navigates on by itself",
              any("generate?code=" in u for u in trail), trail)
        check("the engine's own answer is therefore not the destination",
              hops.is_stop_endpoint(final) is False, final)

        print("\n[2] what the bot reports after reading the trail")
        cfg = Config.load()
        cfg.allow_browser = True
        cfg.max_hops = 10
        cfg.browser_timeout = 20
        e = Engine(cfg)

        class FastSaysBrowser:
            async def resolve(self, url):
                return {"input": url, "final": "", "ok": False, "method": "http",
                        "steps": [], "note": "Still on the shortener page",
                        "blocked": "", "needs_browser": True}

        e.fast = FastSaysBrowser()
        out = asyncio.run(e._one_hop(GATE, True))
        check("final is the auth endpoint", out["final"] == AUTH, out["final"])
        check("not the ad page", "pipefitter" not in out["final"], out["final"])
        check("marked ok", out["ok"] is True)
        check("the trail is kept for the trace", len(out.get("steps") or []) >= 3)
    finally:
        srv.shutdown()

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
