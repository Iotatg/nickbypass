"""Offline tests: no network, no Telegram.

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


print("\n[1] URL extraction")
check("single link", extract_urls("look at this https://gplinks.co/abc123 thanks"), ["https://gplinks.co/abc123"])
check("bare domain", extract_urls("earn4link.in/xyz"), ["https://earn4link.in/xyz"])
check("multiple links", len(extract_urls("a https://a.com/1 b https://b.com/2 c https://c.com/3")), 3)
check("de-duplicated", len(extract_urls("https://a.com/1 https://a.com/1")), 1)
check("cap", len(extract_urls(" ".join(f"https://x{i}.com/p" for i in range(10)))), 5)
check("trailing punctuation", extract_urls("link https://a.com/1."), ["https://a.com/1"])
check("t.me excluded", extract_urls("t.me/somechannel"), [])

print("\n[2] site recognition")
check("gplinks", sites.display_name("https://gplinks.co/abc"), "GPLinks")
check("still matches with a different TLD", sites.display_name("https://gplinks.in/abc"), "GPLinks")
check("oii.la", sites.display_name("https://oii.la/x"), "Oii.la")
check("unknown domain", sites.display_name("https://random-site.xyz/a"), "random-site.xyz")
check("plain redirect needs no browser", sites.needs_browser("https://bit.ly/x"), False)
check("ad gate needs a browser", sites.needs_browser("https://earn4link.in/x"), True)
check("scraper host blocked", sites.is_scraper_only("https://hubcloud.club/drive/abc"), "hubcloud")
check("normal site not blocked", sites.is_scraper_only("https://gplinks.co/x"), None)

print("\n[3] rate limit")
cfg = Config.load()
cfg.rate_limit = 3
cfg.rate_window = 60
rt = Runtime(cfg)
res = [rt.allow(1)[0] for _ in range(4)]
check("first 3 allowed", res, [True, True, True, False])
check("other users unaffected", rt.allow(2)[0], True)
ok, retry = rt.allow(1)
check("retry delay is sane", 0 < retry <= 61, True)

print("\n[4] database")
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
check("user count", st["users"], 1)
check("request count", st["total"], 1)
check("success rate", round(st["success_rate"]), 100)
check("popular sites", st["top"][0][0], "gplinks.co")
check("not banned", db.is_banned(10), False)
db.ban(10, "test")
check("banned", db.is_banned(10), True)
db.unban(10)
check("unbanned", db.is_banned(10), False)
check("user list", db.all_user_ids(), [10])
db.close()

print("\n[5] message layout and escaping")
data_ok = {
    "input": "https://gplinks.co/x", "final": "https://dst.example/a?b=1&c=2",
    "ok": True, "method": "browser", "elapsed": 6.24, "note": "",
    "steps": [{"kind": "request", "url": "https://gplinks.co/x", "status": 200, "detail": "html"}],
}
card = F.result_card(data_ok, 1, 2)
check("success card contains the destination", "dst.example/a?b=1&amp;c=2" in card, True)
check("& escaped", "&amp;" in card, True)
check("numbering and title", card.startswith("✅ <b>Link bypassed</b> · 1/2"), True)
check("success card shows the original link", "You sent" in card and "gplinks.co/x" in card, True)
check("success card ends with a human line", card.count("<i>") >= 2, True)
check("success card has the footer", card.rstrip().endswith("@nova93ec2c4_bot"), True)
check("cached result is marked", "from cache" in F.result_card(dict(data_ok, cached=True)), True)

data_bad = {
    "input": "https://ouo.io/x", "final": "https://ouo.io/x", "ok": False,
    "method": "browser", "elapsed": 12.0, "note": "blocked by Cloudflare（403 just a moment）", "steps": [],
}
card = F.result_card(data_bad)
check("failure card carries a reason", "Cloudflare" in card, True)
check("failure card shows no success mark", "✅" in card, False)
check("failure card explains what happened", "What happened" in card and "What to try" in card, True)
check("failure card does not repeat the raw note", "blocked by Cloudflare" in card, False)

vpn = dict(data_bad, blocked="vpn", note="Site blocks datacenter / VPN IPs", method="http")
card = F.result_card(vpn)
check("IP wall gives actionable advice", "/proxy on IN" in card, True)
check("IP wall does not suggest a browser (same IP)", "browser" in card.lower(), False)

evil = dict(data_ok, final="https://x.example/<script>alert(1)</script>")
check("HTML injection escaped", "<script>" in F.result_card(evil), False)

long_steps = [{"kind": "request", "url": "https://a.example/" + "x" * 80, "status": 200, "detail": "d" * 60} for _ in range(80)]
check("trace truncated", len(F.trace_card({"steps": long_steps})) <= F.MAX_LEN, True)

print("\n[5b] exit IP verdict (/ip)")
from nickbot.handlers import _ip_block, _ip_verdict  # noqa: E402

dc = _ip_block("x", {"query": "1.2.3.4", "countryCode": "SG", "city": "Singapore",
                     "as": "AS132203 Tencent Building, Kejizhongyi Avenue",
                     "isp": "Shenzhen Tencent Computer Systems Company Limited"})
check("datacenter ASN recognised", dc["hosting"], True)
check("ASN keeps only the number", dc["asn"], "AS132203")
check("ISP name truncated", len(dc["isp"]) <= 43, True)
check("datacenter suggests a proxy", "PROXY" in _ip_verdict(dc), True)

jio = _ip_block("x", {"query": "49.36.12.84", "countryCode": "IN", "city": "Mumbai",
                      "as": "AS55836 Reliance Jio Infocomm Limited",
                      "isp": "Reliance Jio Infocomm Limited"})
check("Indian broadband classified as residential", jio["residential"], True)
check("Indian home line needs no proxy", "without any proxy" in _ip_verdict(jio), True)
check("Indian home line suggests turning the pool off", "/proxy off" in _ip_verdict(jio), True)

us = _ip_block("x", {"query": "8.8.8.8", "countryCode": "US", "city": "Mountain View",
                     "as": "AS15169 Google LLC", "isp": "Google LLC"})
check("foreign datacenter does not suggest a pool", "Indian" not in _ip_verdict(us), True)

check("IP card tags are valid", "<b>" in F.ip_card([jio], "ok") and "</b>" in F.ip_card([jio], "ok"), True)
check("lookup failure renders an error line", "timed out" in F.ip_card([{"label": "x", "error": "timed out"}], "v"), True)

print("\n[6] command registration (fake token, no network)")
from nickbot.bot import COMMANDS, build_ptb  # noqa: E402

cfg.token = "123456:AAFakeTokenForOfflineTestOnly"
cfg.backend = "ptb"
app = build_ptb(cfg, rt)
cmds = set()
for group in app.handlers.values():
    for h in group:
        for name in getattr(h, "commands", []) or []:
            cmds.add(name)
need = {"start", "help", "id", "mode", "supported", "status", "ip", "bypass",
        "stats", "users", "ban", "unban", "broadcast"}
check("all commands present", need.issubset(cmds), True)
check("callback handler registered", any("CallbackQueryHandler" in type(h).__name__ for g in app.handlers.values() for h in g), True)

# The Kurigram build must register the same table, and every command has to be
# in the table the menu is generated from.
table = {c for c, _, _ in COMMANDS}
check("command table covers the menu", need - {"help", "supported"} <= table, True)
try:
    import pyrogram  # noqa: E402
    from nickbot.bot import build_kurigram  # noqa: E402

    # Pyrogram queues add_handler() onto the client loop, so record the calls
    # instead of reading dispatcher.groups (which stays empty until start()).
    seen = []
    orig = pyrogram.Client.add_handler

    def record(self, handler, group=0):
        seen.append(type(handler).__name__)
        return handler, group

    pyrogram.Client.add_handler = record
    try:
        cfg.api_id, cfg.api_hash, cfg.backend = 12345, "0123456789abcdef0123456789abcdef", "kurigram"
        kuri = build_kurigram(cfg, rt)
    finally:
        pyrogram.Client.add_handler = orig
    check("kurigram builds a client", isinstance(kuri, pyrogram.Client), True)
    check("kurigram message handlers registered", seen.count("MessageHandler") >= len(COMMANDS), True)
    check("kurigram callback registered", "CallbackQueryHandler" in seen, True)
    check("auto backend picks kurigram", cfg.use_kurigram(), True)
except ImportError:
    print("  - kurigram not installed, skipping that half")
cfg.backend = "auto"
cfg.api_id, cfg.api_hash = 0, ""

print("\n[6b] the pool only wakes up after a wall")


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
check("no pool by default", b0, False)
check("marked as needed after an IP wall", b1, True)

print("\n[7] engine concurrency counters")
async def _conc():
    inflight, waiting = rt.engine.load()
    return (inflight, waiting) == (0, 0)

check("idle at start", asyncio.run(_conc()), True)

print(f"\nResult: {PASS} passed / {FAIL} failed\n")
sys.exit(1 if FAIL else 0)
