"""Fast path (gates.FastResolver) offline tests.

A local HTTP server imitates a "GT Links" ad gate:
    GET  /<slug>      returns a page with /links/go (and sets cookies)
    POST /links/go    {"status":"success","url":"https://target.example/final"}

Covers: direct site API, meta refresh, JS redirect, VPN wall detection,
404 destination.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nickbot import sites                    # noqa: E402
from nickbot.gates import FastResolver       # noqa: E402

PORT = 8799
TARGET = "https://target.example/final?ok=1"
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = self.path
        if p.startswith("/gate/"):
            self._send(200, "<html><body><script>var go='/links/go';</script>"
                            "<p>Please wait 5 seconds</p></body></html>",
                       headers={"Set-Cookie": "gt_uc_=x; Path=/"})
        elif p == "/meta":
            self._send(200, '<html><head><meta http-equiv="refresh" content="0;url=https://dest.example/a"></head></html>')
        elif p == "/js":
            self._send(200, '<html><script>window.location.href="https://dest.example/b";</script></html>')
        elif p == "/vpn":
            self._send(200, "<html><body><h2>VPN Detected</h2>"
                            "<p>You're using a VPN or Proxy. Please Turn off VPN to access website.</p></body></html>")
        elif p == "/dead":
            self._send(302, "", headers={"Location": "https://dead.example/gone"})
        else:
            self._send(404, "not found")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        if self.path.startswith("/links/go"):
            form = dict(x.split("=", 1) for x in body.split("&") if "=" in x)
            if form.get("link"):
                self._send(200, json.dumps({"status": "success", "message": "", "url": TARGET}),
                           ctype="application/json")
            else:
                self._send(200, json.dumps({"status": "error", "message": "missing link"}),
                           ctype="application/json")
        else:
            self._send(404, "no")


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"

    async def run():
        r = FastResolver(timeout=8)
        print("\n[1] direct site API (GT Links flow)")
        d = await r.resolve(f"{base}/gate/abc123")
        check("ok", d["ok"], d.get("note"))
        check("site API hit", d["method"] == "gate", d["method"])
        check("destination obtained", d["final"] == TARGET, d["final"])
        check("took < 2s", d["elapsed"] < 2.0, f"{d['elapsed']:.2f}s")

        print("\n[2] meta refresh")
        d = await r.resolve(f"{base}/meta")
        check("ok", d["ok"])
        check("destination is correct", d["final"] == "https://dest.example/a", d["final"])

        print("\n[3] JS location")
        d = await r.resolve(f"{base}/js")
        check("ok", d["ok"])
        check("destination is correct", d["final"] == "https://dest.example/b", d["final"])

        print("\n[4] VPN wall detection (no more 30s browser waste)")
        d = await r.resolve(f"{base}/vpn")
        check("failed", not d["ok"])
        check("marked blocked=vpn", d["blocked"] == "vpn", d["blocked"])
        check("mentions the proxy", "residential proxy" in d["note"].lower(), d["note"][:60])
        check("took < 1s", d["elapsed"] < 1.0, f"{d['elapsed']:.2f}s")

        print("\n[5] short link to a dead destination")
        d = await r.resolve(f"{base}/dead")
        check("still counts as resolved", d["ok"], d.get("note"))
        check("keeps the destination address", d["final"] == "https://dead.example/gone", d["final"])

        print("\n[6] 5 concurrent links do not block each other")
        import time as _t
        t0 = _t.perf_counter()
        res = await asyncio.gather(*[r.resolve(f"{base}/gate/c{i}") for i in range(5)])
        dt = _t.perf_counter() - t0
        check("all succeeded", all(x["ok"] for x in res))
        check("total time < 2s", dt < 2.0, f"{dt:.2f}s")

        await r.aclose()

    asyncio.run(run())
    srv.shutdown()

    print("\n[7] shortener admits the link is dead (the bit.ly warning page)")
    from nickbot import gates as G

    real = G._site_of
    G._site_of = lambda url: sites.Site("bit.ly", "Bit.ly", sites.KIND_SHORT) \
        if "bit.ly" in url else None
    warn = ("<html><head><title>Warning!</title></head><body>"
            "There might be a problem with the requested link</body></html>")
    check("shortener -> judged dead", G._dead_link(warn, "https://bit.ly/3xYz") is True)
    check("normal site -> not misjudged", G._dead_link(warn, "https://example.com/blog/x") is False)
    check("shortener normal page -> not dead", G._dead_link("<html>hello</html>", "https://bit.ly/x") is False)
    G._site_of = real

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
