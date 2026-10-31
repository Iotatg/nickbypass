"""站点注册表的单测：数量、去重、命中规则、性能。

    python3 tests/test_registry.py
"""

import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nickbot import registry, sites     # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"\n      got={got!r}\n      want={want!r}"))


print("\n[1] 规模")
st = sites.stats()
check("总数 > 500", st["total"] > 500, True)
check("总数 > 2000", st["total"] > 2000, True)
check("手工层 150+", st["curated"] >= 150, True)
check("生成层 1000+", st["listed"] >= 1000, True)
check("手写 gate/gt 100+", st["gates"] >= 100, True)
check("其中 gt 家族 40+", st["gt"] >= 40, True)
check("纯跳转 30+", st["short"] >= 30, True)
print(f"      total={st['total']} curated={st['curated']} listed={st['listed']}")

print("\n[2] 数据卫生")
curated = [d for d, _, _ in registry.CURATED]
check("手工层无重复", len(curated), len(set(curated)))
check("两层无重叠", len(set(curated) & set(sites.LISTED)), 0)
check("生成层无重复", len(sites.LISTED), len(set(sites.LISTED)))
HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
bad = [d for d in sites.LISTED if not HOST.match(d)]
check("生成层全是合法域名", bad, [])
bad = [d for d in curated if not HOST.match(d)]
check("手工层全是合法域名", bad, [])
bad = [d for d in curated + list(sites.LISTED)
       if d.startswith(("10.", "127.", "192.168.", "172.")) or ".." in d or "*" in d]
check("没有内网 / 通配符", bad, [])
check("没有协议前缀", [d for d in sites.LISTED if "://" in d], [])
check("全是小写", [d for d in curated + list(sites.LISTED) if d != d.lower()], [])
check("kind 只有四种", {k for _, _, k in registry.CURATED}, {"gt", "gate", "short"})

print("\n[3] 命中规则")
check("精确命中", sites.display_name("https://gplinks.co/abc"), "GPLinks")
check("换后缀命中", sites.display_name("https://gplinks.xyz/abc"), "GPLinks")
check("子域名命中", sites.display_name("https://go.gplinks.co/abc"), "GPLinks")
check("子域名命中 2", sites.display_name("https://drop.carbikenation.com/x"), "Drop (Carbike Nation)")
check("未知域名原样返回", sites.display_name("https://random-site.xyz/a"), "random-site.xyz")
check("空串不炸", sites.display_name(""), "unknown")
check("垃圾输入不炸", sites.display_name("not a url"), "unknown")
check("大小写不敏感", sites.display_name("https://GPlinks.CO/x"), "GPLinks")
check("生成层认得出", sites.lookup("https://shrtfly.com/abc").kind, sites.KIND_LISTED)
check("生成层名字=域名", sites.display_name("https://shrtfly.com/abc"), "shrtfly.com")
check("t.co 不会被 net.com 抢走", sites.lookup("https://net.com/x"), None)
check("bit.ly 命中 short", sites.lookup("https://bit.ly/x").kind, "short")
check("gt 家族认得", sites.lookup("https://tii.la/x").kind, "gt")

print("\n[4] 该不该上浏览器")
check("short 不用浏览器", sites.needs_browser("https://bit.ly/x"), False)
check("gate 要浏览器", sites.needs_browser("https://adf.ly/x"), True)
check("gt 要浏览器", sites.needs_browser("https://gplinks.co/x"), True)
check("生成层不确定=要", sites.needs_browser("https://shrtfly.com/x"), True)
check("未知域名=要", sites.needs_browser("https://random-site.xyz/x"), True)

print("\n[5] 终点判定（别把广告页当结果）")
check("gate 短码不是终点", sites.is_gate("https://gplinks.co/abc123"), True)
check("生成层短码不是终点", sites.is_gate("https://shrtfly.com/abc123"), True)
check("生成层深路径算终点", sites.is_gate("https://shrtfly.com/blog/some-post"), False)
check("生成层根路径算终点", sites.is_gate("https://shrtfly.com/"), False)
check("short 短码也不是终点", sites.is_gate("https://bit.ly/abc"), True)
check("short 深路径算终点", sites.is_gate("https://bit.ly/blog/post-1"), False)
check("未知域名算终点", sites.is_gate("https://example.org/abc"), False)

print("\n[6] 文案")
from html.parser import HTMLParser   # noqa: E402


class Balance(HTMLParser):
    """Telegram 只认闭合良好的标签，这里数一遍。"""

    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.bad.append(tag)
        else:
            self.stack.pop()


def balanced(text):
    p = Balance()
    p.feed(text)
    return not p.bad and not p.stack


txt = sites.supported_text()
check("有总数", "sites on the list" in txt, True)
check("长度可控", len(txt) <= 4000, True)
check("标签闭合", balanced(txt), True)
check("没有未转义的 &", "&" in txt.replace("&amp;", "").replace("&lt;", "").replace("&gt;", ""), False)
check("查询命中", "GPLinks" in sites.search_text("gplinks"), True)
check("查询标签闭合", balanced(sites.search_text("gplinks")), True)
check("查询域名", "shrtfly.com" in sites.search_text("shrtfly"), True)
check("查询落空有解释", "generic engine" in sites.search_text("zzzz-nope"), True)
check("空查询=全表", sites.search_text(""), sites.supported_text())

print("\n[7] 性能")
t = time.perf_counter()
for _ in range(2000):
    sites.lookup("https://gplinks.co/abc")
dt = time.perf_counter() - t
check("2000 次命中 < 0.5s", dt < 0.5, True)
t = time.perf_counter()
for _ in range(2000):
    sites.lookup("https://unknown-domain-xyz.com/a")
dt = time.perf_counter() - t
check("2000 次未命中 < 0.5s", dt < 0.5, True)

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
