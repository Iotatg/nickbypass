"""端到端链路测试：复刻 vplink.in 的真实广告链形状。

真实链路（2026-10 实测，来自 vplink.in/nAKfh）：

    vplink.in/nAKfh            200  "Please Wait.. Opening Link"
                                 window.location.href = "https:\\/\\/pipefitter.in\\/..."
    pipefitter.in/...          200  "Landing.."  "Generating Link"
                                 window.location.href = "https:\\/\\/pipefitter.in\\/article\\/..."
    ...
    auth.pwasmultiverse.workers.dev/generate?code=...   终点

这里用本地服务复刻前两跳的形状（JS 里 URL 带 \\/ 转义、文案含 "Please Wait"
和 "Landing.."），验证解析器能不能一路跟到终点。能跟到，就说明 vplink 失败
的唯一原因是出口 IP 被墙，而不是引擎不认这种页面。
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

# 终点页把用户弹回广告层（真实链路里 targetssc.com -> pipefitter.in 就是这样）
REDIRECTS = {
    "/clean": "https://pipefitter.in/studyinsurances/",
}

# 从"广告门"跳到 localhost 上的终点页（localhost 与 127.0.0.1 算不同站点）
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
        print("\n[1] 复刻 vplink 形状的三跳链路")
        t = time.perf_counter()
        d = await r.resolve(f"{BASE}/nAKfh")
        took = time.perf_counter() - t

        check("解析成功", d["ok"], d.get("note", ""))
        check("终点是 /generate?code=…", d["final"].endswith("/generate?code=N8BCN7VQDAZFL48O"), d["final"])
        check("跳数 >= 3", len(d["steps"]) >= 3, str(len(d["steps"])))
        check("用时 < 2s", took < 2.0, f"{took:.2f}s")
        check("没有被误判成需要浏览器", not d.get("needs_browser"), str(d.get("needs_browser")))

        print("\n[2] 转义斜杠 \\/ 必须还原")
        check("没有残留反斜杠", "\\/" not in d["final"], d["final"])

        print("\n[3] 第二次走缓存以外仍然正确（新实例）")
        r2 = FastResolver(timeout=8)
        d2 = await r2.resolve(f"{BASE}/landing")
        check("从中途站继续也能到终点", d2["ok"] and d2["final"].endswith("code=N8BCN7VQDAZFL48O"), d2["final"])
        await r2.aclose()

        print("\n[4] 终点把用户弹回广告层时，不能把广告页当结果")
        # localhost 与 127.0.0.1 视为不同站点：/clean 已经是"终点"，
        # 它 301 到 pipefitter.in（广告层）时必须停在 /clean。
        d3 = await r2.resolve(f"{BASE}/bounce")
        check("解析成功", d3["ok"], d3.get("note", ""))
        check("终点停在 /clean", d3["final"].endswith("/clean"), d3["final"])
        check("没有跟到 pipefitter", "pipefitter" not in d3["final"], d3["final"])
    finally:
        await r.aclose()
        srv.shutdown()

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
