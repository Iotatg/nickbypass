"""离线单测：不联网，只验证提取器逻辑。

    python3 tests/test_offline.py
"""

import base64
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linkresolver import rules as R          # noqa: E402
from linkresolver.engine import is_blocked, normalize   # noqa: E402

PASS, FAIL = 0, 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"\n      got={got!r}\n      want={want!r}"))


def b64(s):
    return base64.b64encode(s.encode()).decode()


def ysmm_of(target: str, salt: str = "0123456789abcdef") -> str:
    """按 adf.ly 的算法反向构造 ysmm 变量（前半段 + 后半段倒序 交错）。"""
    s = b64(salt + target)
    half = len(s) // 2
    a, b = s[:half], s[half:][::-1]
    out = []
    for i in range(len(s)):
        out.append(a[i // 2] if i % 2 == 0 else b[i // 2])
    return "".join(out)


print("\n[1] 基础归一化")
check("补 https", normalize("bit.ly/abc"), "https://bit.ly/abc")
check("保留 http", normalize("http://x.y/z"), "http://x.y/z")
check("去空格引号", normalize("  'https://a.b' "), "https://a.b")

print("\n[2] meta refresh")
html = '<html><head><meta http-equiv="refresh" content="0; url=https://dest.example/p?q=1"></head></html>'
check("meta 提取", R.extract_next("https://short.test/a", html)[0], "https://dest.example/p?q=1")

print("\n[3] JS location")
html = '<script>window.location.href = "https://dest.example/b";</script>'
check("js 提取", R.extract_next("https://short.test/a", html)[0], "https://dest.example/b")
html = "<script>location.replace('/final?id=9')</script>"
check("js 相对路径", R.extract_next("https://short.test/a", html)[0], "https://short.test/final?id=9")

print("\n[4] base64 参数")
u = "https://dest.example/c?x=1"
check("?url=", R.extract_next(f"https://short.test/go?url={b64(u)}", "<p>wait</p>")[0], u)
check("?r= 无 padding", R.extract_next(f"https://short.test/go?r={b64(u).rstrip('=')}", "<p>wait</p>")[0], u)

print("\n[5] adf.ly ysmm")
tgt = "https://dest.example/adfly-target"
html = f"<script>var ysmm = '{ysmm_of(tgt)}';</script>"
check("ysmm 解码", R.extract_next("https://adf.ly/abc", html)[0], tgt)

print("\n[6] 页面按钮直达（v.gd 型）")
html = '<a href="https://dest.example/e" class="biglink">https://dest.example/e</a>'
check("biglink", R.extract_next("https://v.gd/xyz", html)[0], "https://dest.example/e")
html = '<a href="https://dest.example/f">Continue to download</a>'
check("continue 文案", R.extract_next("https://short.test/x", html)[0], "https://dest.example/f")

print("\n[7] GET 表单自动提交")
html = ('<form action="/go" method="get">'
        '<input type="hidden" name="ne" value="https://dest.example/g">'
        '<input type="hidden" name="t" value="7"></form>')
nxt = R.extract_next("https://short.test/x", html)[0]
check("表单拼参数", nxt.startswith("https://short.test/go?"), True)
check("表单带目标", "dest.example" in nxt, True)

print("\n[8] 反爬识别")
blocked, why = is_blocked(403, "<title>Just a moment...</title>")
check("403+cf", blocked, True)
blocked, _ = is_blocked(200, "<html><body>hello</body></html>")
check("正常页不误判", blocked, False)

print("\n[9] 不应把静态资源当终点")
html = '<script>var a = "https://cdn.example/app.js";</script>'
check("过滤 .js", R.extract_next("https://short.test/x", html), None)

print(f"\n结果：{PASS} 通过 / {FAIL} 失败\n")
sys.exit(1 if FAIL else 0)
