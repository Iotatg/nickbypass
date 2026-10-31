"""离线单测：不联网、不连 Telegram。

    python3 tests/test_offline.py
"""

import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nickbot import format as F           # noqa: E402
from nickbot import sites                # noqa: E402
from nickbot.config import Config        # noqa: E402
from nickbot.db import DB                # noqa: E402
from nickbot.handlers import Runtime, extract_urls   # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS += ok
    FAIL += (not ok)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"\n      got={got!r}\n      want={want!r}"))


print("\n[1] 链接提取")
check("单链接", extract_urls("看看这个 https://gplinks.co/abc123 谢谢"), ["https://gplinks.co/abc123"])
check("裸域名", extract_urls("earn4link.in/xyz"), ["https://earn4link.in/xyz"])
check("多链接", len(extract_urls("a https://a.com/1 b https://b.com/2 c https://c.com/3")), 3)
check("去重", len(extract_urls("https://a.com/1 https://a.com/1")), 1)
check("上限", len(extract_urls(" ".join(f"https://x{i}.com/p" for i in range(10)))), 5)
check("尾部标点", extract_urls("链接 https://a.com/1。"), ["https://a.com/1"])
check("排除 t.me", extract_urls("t.me/somechannel"), [])

print("\n[2] 站点识别")
check("gplinks", sites.display_name("https://gplinks.co/abc"), "GPLinks")
check("换 TLD 仍命中", sites.display_name("https://gplinks.in/abc"), "GPLinks")
check("oii.la", sites.display_name("https://oii.la/x"), "Oii.la")
check("未知域名", sites.display_name("https://random-site.xyz/a"), "random-site.xyz")
check("纯跳转不需浏览器", sites.needs_browser("https://bit.ly/x"), False)
check("广告门需浏览器", sites.needs_browser("https://earn4link.in/x"), True)
check("爬虫类拦截", sites.is_scraper_only("https://hubcloud.club/drive/abc"), "hubcloud")
check("正常站不拦", sites.is_scraper_only("https://gplinks.co/x"), None)

print("\n[3] 限流")
cfg = Config.load()
cfg.rate_limit = 3
cfg.rate_window = 60
rt = Runtime(cfg)
res = [rt.allow(1)[0] for _ in range(4)]
check("前3次放行", res, [True, True, True, False])
check("别的用户不受影响", rt.allow(2)[0], True)
ok, retry = rt.allow(1)
check("重试时间合理", 0 < retry <= 61, True)

print("\n[4] 数据库")
db = DB(pathlib.Path("/tmp/_nickbot_test.db"))
for p in ("/tmp/_nickbot_test.db",):
    try:
        pathlib.Path(p).unlink()
    except Exception:
        pass
db = DB(pathlib.Path("/tmp/_nickbot_test.db"))
db.touch_user(10, "alice", "Alice")
db.touch_user(10, "alice", "Alice")
db.bump_user(10)
db.log_request(10, "gplinks.co", "https://gplinks.co/x", "https://dst.example/y", True, "browser", 6.2)
st = db.stats()
check("用户数", st["users"], 1)
check("请求数", st["total"], 1)
check("成功率", round(st["success_rate"]), 100)
check("热门站", st["top"][0][0], "gplinks.co")
check("未封禁", db.is_banned(10), False)
db.ban(10, "test")
check("已封禁", db.is_banned(10), True)
db.unban(10)
check("已解封", db.is_banned(10), False)
check("用户列表", db.all_user_ids(), [10])
db.close()

print("\n[5] 消息排版与转义")
data_ok = {
    "input": "https://gplinks.co/x", "final": "https://dst.example/a?b=1&c=2",
    "ok": True, "method": "browser", "elapsed": 6.24, "note": "",
    "steps": [{"kind": "request", "url": "https://gplinks.co/x", "status": 200, "detail": "html"}],
}
card = F.result_card(data_ok, 1, 2)
check("成功卡片含终点", "dst.example/a?b=1&amp;c=2" in card, True)
check("& 已转义", "&amp;" in card, True)
check("编号与标题", card.startswith("✅ <b>Link bypassed</b> · 1/2"), True)
check("成功卡片有原文链接", "You sent" in card and "gplinks.co/x" in card, True)
check("成功卡片有一句人话结尾", card.count("<i>") >= 2, True)
check("成功卡片带页脚", card.rstrip().endswith("@nova93ec2c4_bot"), True)
check("缓存结果标注", "from cache" in F.result_card(dict(data_ok, cached=True)), True)

data_bad = {
    "input": "https://ouo.io/x", "final": "https://ouo.io/x", "ok": False,
    "method": "browser", "elapsed": 12.0, "note": "被 Cloudflare 拦（403 just a moment）", "steps": [],
}
card = F.result_card(data_bad)
check("失败卡片带原因", "Cloudflare" in card, True)
check("失败不显示成功标记", "✅" in card, False)
check("失败卡片解释发生了什么", "What happened" in card and "What to try" in card, True)
check("失败卡片不再重复原始 note", "被 Cloudflare 拦" in card, False)

vpn = dict(data_bad, blocked="vpn", note="Site blocks datacenter / VPN IPs", method="http")
card = F.result_card(vpn)
check("IP 墙给出可执行建议", "/proxy on IN" in card, True)
check("IP 墙不推浏览器（同 IP 无用）", "browser" in card.lower(), False)

evil = dict(data_ok, final="https://x.example/<script>alert(1)</script>")
check("HTML 注入被转义", "<script>" in F.result_card(evil), False)

long_steps = [{"kind": "request", "url": "https://a.example/" + "x" * 80, "status": 200, "detail": "d" * 60} for _ in range(80)]
check("轨迹截断", len(F.trace_card({"steps": long_steps})) <= F.MAX_LEN, True)

print("\n[5b] 出口 IP 判定（/ip）")
from nickbot.handlers import _ip_block, _ip_verdict  # noqa: E402

dc = _ip_block("x", {"query": "1.2.3.4", "countryCode": "SG", "city": "Singapore",
                     "as": "AS132203 Tencent Building, Kejizhongyi Avenue",
                     "isp": "Shenzhen Tencent Computer Systems Company Limited"})
check("机房 ASN 被认出", dc["hosting"], True)
check("ASN 只留编号", dc["asn"], "AS132203")
check("ISP 名称被截断", len(dc["isp"]) <= 43, True)
check("机房建议要代理", "PROXY" in _ip_verdict(dc), True)

jio = _ip_block("x", {"query": "49.36.12.84", "countryCode": "IN", "city": "Mumbai",
                      "as": "AS55836 Reliance Jio Infocomm Limited",
                      "isp": "Reliance Jio Infocomm Limited"})
check("印度宽带被判住宅", jio["residential"], True)
check("印度住宅说不需要代理", "without any proxy" in _ip_verdict(jio), True)
check("印度住宅建议关掉池子", "/proxy off" in _ip_verdict(jio), True)

us = _ip_block("x", {"query": "8.8.8.8", "countryCode": "US", "city": "Mountain View",
                     "as": "AS15169 Google LLC", "isp": "Google LLC"})
check("外国机房不建议开池子", "Indian" not in _ip_verdict(us), True)

check("IP 卡片标签合法", "<b>" in F.ip_card([jio], "ok") and "</b>" in F.ip_card([jio], "ok"), True)
check("查不到时给错误行", "timed out" in F.ip_card([{"label": "x", "error": "timed out"}], "v"), True)

print("\n[6] 命令注册（用假 token 构建，不联网）")
from nickbot.bot import build  # noqa: E402

cfg.token = "123456:AAFakeTokenForOfflineTestOnly"
app = build(cfg)
cmds = set()
for group in app.handlers.values():
    for h in group:
        for name in getattr(h, "commands", []) or []:
            cmds.add(name)
need = {"start", "help", "id", "mode", "supported", "status", "ip", "bypass", "stats", "users", "ban", "unban", "broadcast"}
check("命令齐全", need.issubset(cmds), True)
check("有回调处理", any("CallbackQueryHandler" in type(h).__name__ for g in app.handlers.values() for h in g), True)

print("\n[6b] 代理池只在被墙之后才启动")


class _StubFast:
    async def resolve(self, url):
        return {"input": url, "final": url, "ok": False, "blocked": "vpn",
                "note": "VPN detected", "steps": [], "method": "http"}


async def _wall():
    e = rt.engine
    e.farm = None
    e.pool = None
    before = e.farm_needed
    e.fast = _StubFast()
    await e._resolve_inner("https://vplink.in/x", False)
    return before, e.farm_needed


b0, b1 = asyncio.run(_wall())
check("默认不维护代理池", b0, False)
check("撞到 IP 墙后标记需要", b1, True)

print("\n[7] 引擎并发计数")
async def _conc():
    inflight, waiting = rt.engine.load()
    return (inflight, waiting) == (0, 0)

check("初始空闲", asyncio.run(_conc()), True)

print(f"\n结果：{PASS} 通过 / {FAIL} 失败\n")
sys.exit(1 if FAIL else 0)
