"""End-to-end chain test: reproduces the real ad chain shape of vplink.in.

The real chain (measured 2026-10, from vplink.in/nAKfh):

    vplink.in/nAKfh            200  "Please Wait.. Opening Link"
                                 window.location.href = "https:\\/\\/pipefitter.in\\/..."
    pipefitter.in/...          200  "Landing.."  "Generating Link"
                                 window.location.href = "https:\\/\\/pipefitter.in\\/article\\/..."
    ...
    auth.pwasmultiverse.workers.dev/generate?code=...   destination

The local server below reproduces the shape of the first two hops (escaped
slashes in the JS URL, "Please Wait" / "Landing.." copy) to prove the resolver
can follow the chain all the way to the destination. If it can, then the only
reason vplink fails in production is a blocked exit IP, not the page format.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nickbot.gates import FastResolver  # noqa: E402

PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"

PAGES = {
    "/nAKfh": """<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="referrer" content="no-referrer"><title>Please Wait..</title></head>
<body><center><b><h1 style="font-size:5rem">Please Wait..</h1><br/>
<h2>Opening Link</h2></b></center>
<script type="text/javascript">
window.location.href = "http:\\/\\/127.0.0.1:%d\\/landing?insurancesstudy=nAKfh&uiso=12608";
</script></body></html>""" % PORT,

    "/landing": """<!DOCTYPE html><html><head><title>Landing..</title>
<meta name="referrer" content="no-referrer"></head>
<body><div class="center-ad"><center><p></p>
http://127.0.0.1:%d/article/best-premium-cards/<br><p>Generating Link</p>
<b>Please Wait...<br></b>
<script type="text/javascript">
window.location.href = "http:\\/\\/127.0.0.1:%d\\/article\\/best-premium-cards\\/";
</script></center></div></body></html>""" % (PORT, PORT),

    "/article/best-premium-cards/": """<!DOCTYPE html><html><head><title>Cards</title></head>
<body><h1>Article</h1><p>ad content</p>
<script>setTimeout(function(){location.replace("http://127.0.0.1:%d/generate?code=N8BCN7VQDAZFL48O");}, 0);</script>
</body></html>""" % PORT,

    "/generate": """<!DOCTYPE html><html><head><title>Link hub</title></head>
<body><h1>You reached the destination</h1><p>code accepted</p></body></html>""",
}

# The destination bounces the user back into the ad layer (targetssc.com ->
    # pipefitter.in does exactly that in the real chain)
REDIRECTS = {
    "/clean": "https://pipefitter.in/studyinsurances/",
}

# Jump from the ad gate to a destination page on localhost (localhost and
    # 127.0.0.1 count as different sites)
PAGES["/bounce"] = """<!DOCTYPE html><html><head><title>Please Wait..</title></head>
<body><h2>Opening Link</h2>
<script>window.location.href = "http://localhost:%d/clean";</script></body></html>""" % PORT


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path in REDIRECTS:
            self.send_response(301)
            self.send_header("Location", REDIRECTS[path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = PAGES.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"nope")
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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


async def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    r = FastResolver(timeout=8, max_hops=12)
    try:
        print("\n[1] three-hop chain shaped like vplink")
        t = time.perf_counter()
        d = await r.resolve(f"{BASE}/nAKfh")
        took = time.perf_counter() - t

        check("resolved", d["ok"], d.get("note", ""))
        check("destination is /generate?code=...", d["final"].endswith("/generate?code=N8BCN7VQDAZFL48O"), d["final"])
        check("hops >= 3", len(d["steps"]) >= 3, str(len(d["steps"])))
        check("took < 2s", took < 2.0, f"{took:.2f}s")
        check("not misjudged as needing a browser", not d.get("needs_browser"), str(d.get("needs_browser")))

        print("\n[2] escaped slashes \\/ must be restored")
        check("no stray backslashes", "\\/" not in d["final"], d["final"])

        print("\n[3] still correct on a second, uncached run (new instance)")
        r2 = FastResolver(timeout=8)
        d2 = await r2.resolve(f"{BASE}/landing")
        check("resuming from a waypoint still reaches the destination", d2["ok"] and d2["final"].endswith("code=N8BCN7VQDAZFL48O"), d2["final"])
        await r2.aclose()

        print("\n[4] a destination bouncing back into the ad layer is not the result")
        # localhost and 127.0.0.1 count as different sites: /clean already is the
        # destination,
        # so a 301 to pipefitter.in (the ad layer) has to stop at /clean.
        d3 = await r2.resolve(f"{BASE}/bounce")
        check("resolved", d3["ok"], d3.get("note", ""))
        check("destination stops at /clean", d3["final"].endswith("/clean"), d3["final"])
        check("did not follow through to pipefitter", "pipefitter" not in d3["final"], d3["final"])
    finally:
        await r.aclose()
        srv.shutdown()

    print(f"\nResult: {passed} passed / {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
