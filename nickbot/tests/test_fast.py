"""快路径（gates.FastResolver）离线测试。

用一个本地 HTTP 服务模拟 "GT Links" 广告门：
    GET  /<slug>      返回带 /links/go 的页面（并下发 cookie）
    POST /links/go    {"status":"success","url":"https://target.example/final"}

验证：站点 API 直取、meta refresh、JS 跳转、VPN 墙识别、404 终点。
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

from nickbot.gates import FastResolver  # noqa: E402

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
        print("\n[1] 站点 API 直取（GT Links 流程）")
        d = await r.resolve(f"{base}/gate/abc123")
        check("ok", d["ok"], d.get("note"))
        check("命中站点 API", d["method"] == "gate", d["method"])
        check("拿到目标", d["final"] == TARGET, d["final"])
        check("用时 < 2s", d["elapsed"] < 2.0, f"{d['elapsed']:.2f}s")

        print("\n[2] meta refresh")
        d = await r.resolve(f"{base}/meta")
        check("ok", d["ok"])
        check("终点正确", d["final"] == "https://dest.example/a", d["final"])

        print("\n[3] JS location")
        d = await r.resolve(f"{base}/js")
        check("ok", d["ok"])
        check("终点正确", d["final"] == "https://dest.example/b", d["final"])

        print("\n[4] VPN 墙识别（不再浪费 30s 开浏览器）")
        d = await r.resolve(f"{base}/vpn")
        check("失败", not d["ok"])
        check("标记 blocked=vpn", d["blocked"] == "vpn", d["blocked"])
        check("提示代理", "residential proxy" in d["note"].lower(), d["note"][:60])
        check("用时 < 1s", d["elapsed"] < 1.0, f"{d['elapsed']:.2f}s")

        print("\n[5] 短链跳到已失效终点")
        d = await r.resolve(f"{base}/dead")
        check("仍算解析成功", d["ok"], d.get("note"))
        check("保留终点地址", d["final"] == "https://dead.example/gone", d["final"])

        print("\n[6] 并发 5 条互不阻塞")
        import time as _t
        t0 = _t.perf_counter()
        res = await asyncio.gather(*[r.resolve(f"{base}/gate/c{i}") for i in range(5)])
        dt = _t.perf_counter() - t0
        check("全部成功", all(x["ok"] for x in res))
        check("总耗时 < 2s", dt < 2.0, f"{dt:.2f}s")

        await r.aclose()

    asyncio.run(run())
    srv.shutdown()

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
