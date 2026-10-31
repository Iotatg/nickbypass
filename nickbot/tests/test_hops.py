"""Final-destination classification and the multi-hop loop (offline).

The bug these tests lock down: the bot answered

    https://auth.pwasmultiverse.workers.dev/generate?code=N8BCN7VQDAZFL48O

as the "Bypassed Link" while the real page was still further along. A generated
auth endpoint is a waypoint, not a destination — but neither is every URL that
happens to contain the word "auth", so the classifier is scored, not keyword
matched.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nickbot import hops                    # noqa: E402
from nickbot.config import Config           # noqa: E402
from nickbot.resolver import Engine         # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


GATE = "https://auth.pwasmultiverse.workers.dev/generate?code=N8BCN7VQDAZFL48O"
FINAL = ("https://targetssc.com/studyinsurances/"
         "best-short-term-health-insurance-in-usa-2026/")


def verdict(url, **kw):
    return hops.is_intermediate(url, **kw)


def main() -> int:
    print("\n[1] waypoints are recognised")
    check("generated auth endpoint", verdict(GATE).intermediate is True)
    check("...and says why", "gate path" in verdict(GATE).why or "workers.dev" in verdict(GATE).why)
    check("known shortener", verdict("https://gplinks.co/abc123").intermediate is True)
    check("known gate", verdict("https://vplink.in/nAKfh").intermediate is True)
    check("open redirector", verdict(
        "https://example.com/redirect?url=https%3A%2F%2Fevil.example%2Fx").intermediate is True)
    check("/go/ hop", verdict("https://example.net/go/9f8e7d").intermediate is True)
    check("/verify with token", verdict(
        "https://site.example/verify?token=AB12cd34EF56").intermediate is True)
    check("continue page", verdict("https://site.example/continue/step2").intermediate is True)
    check("same host as previous hop tips a weak signal over",
          verdict("https://site.example/generate", prev_url="https://site.example/x").intermediate is True)
    check("page text can decide it", verdict(
        "https://site.example/p", text="Please wait... redirecting you in 5 seconds").intermediate is True)

    print("\n[2] real destinations are left alone")
    check("the real target", verdict(FINAL).intermediate is False)
    check("article slug", verdict("https://example.org/blog/post-1").intermediate is False)
    check("article about generating links", verdict(
        "https://news.site/article/how-to-generate-more-links").intermediate is False)
    check("auth in a slug", verdict(
        "https://medium.com/@nick/why-i-love-auth-systems-1234abcd").intermediate is False)
    check("shop coupon code", verdict(
        "https://shop.example.com/product?code=SUMMER20").intermediate is False)
    check("verify-email page", verdict("https://example.com/verify-email").intermediate is False)
    check("deep path on a shortener host", verdict(
        "https://shrtfly.com/blog/some-post").intermediate is False)
    check("plain page", verdict("https://example.com/about").intermediate is False)
    check("content page with utm tags", verdict(
        "https://site.example/blog/how-to-do-x?utm_source=tg").intermediate is False)
    check("article title down-weights", verdict(
        "https://site.example/generate", title="How to generate a strong password safely").intermediate is False)

    print("\n[3] hop loop: intermediate -> final")
    cfg = Config.load()
    cfg.allow_browser = True
    cfg.max_hops = 10
    e = Engine(cfg)

    calls = []

    async def fake_hop(url, allow, force_browser=False):
        calls.append(url)
        if url.startswith("https://vplink.in/"):
            return {"final": GATE, "ok": True, "method": "browser", "steps": [], "note": ""}
        if url == GATE:
            return {"final": FINAL, "ok": True, "method": "http", "steps": [], "note": ""}
        return {"final": url, "ok": False, "method": "http", "steps": [], "note": "?"}

    e._one_hop = fake_hop
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("final is the real destination", out["final"] == FINAL, out["final"])
    check("marked ok", out["ok"] is True)
    check("status ok", out.get("status") == "ok")
    check("two hops recorded", len(out["hops"]) == 2, out.get("hops"))
    check("first hop flagged intermediate", out["hops"][0]["intermediate"] is True)
    check("second hop not flagged", out["hops"][1]["intermediate"] is False)
    check("both URLs were actually resolved", calls[:2] == ["https://vplink.in/nAKfh", GATE])

    print("\n[3b] HTTP decoy, browser gets the real exit")
    seen_calls = []

    async def decoy(url, allow, force_browser=False):
        seen_calls.append((url, force_browser))
        if force_browser:
            return {"final": FINAL, "ok": True, "method": "browser", "steps": [], "note": ""}
        return {"final": GATE, "ok": True, "method": "http", "steps": [], "note": ""}

    e._one_hop = decoy
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("browser answer wins", out["final"] == FINAL, out["final"])
    check("marked ok", out["ok"] is True)
    check("one hop, not two", len(out["hops"]) == 1, len(out["hops"]))
    check("browser was retried on the gate page",
          any(u == "https://vplink.in/nAKfh" and f for u, f in seen_calls))

    print("\n[4] a dead end is not reported as success")
    async def stuck(url, allow, force_browser=False):
        return {"final": GATE, "ok": True, "method": "http", "steps": [], "note": ""}

    e._one_hop = stuck
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("intermediate is never the answer", out["final"] == GATE)
    check("...but it is not ok", out["ok"] is False)
    check("status loop", out.get("status") == "loop", out.get("status"))
    check("note explains it", "intermediate" in out["note"].lower())

    print("\n[5] loop detection and hop budget")
    async def pingpong(url, allow, force_browser=False):
        nxt = GATE if url != GATE else "https://vplink.in/nAKfh"
        return {"final": nxt, "ok": True, "method": "http", "steps": [], "note": ""}

    e._one_hop = pingpong
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("bouncing chain stops", out["ok"] is False)
    check("stops quickly", len(out["hops"]) <= 3, len(out["hops"]))

    cfg2 = Config.load()
    cfg2.allow_browser = False
    cfg2.max_hops = 3
    e2 = Engine(cfg2)
    counter = {"n": 0}

    async def endless(url, allow, force_browser=False):
        counter["n"] += 1
        return {"final": f"https://auth{counter['n']}.workers.dev/generate?code=AB12CD34EF",
                "ok": True, "method": "http", "steps": [], "note": ""}

    e2._one_hop = endless
    out = asyncio.run(e2._resolve_inner("https://vplink.in/nAKfh", False))
    check("hop budget enforced", counter["n"] == 3, counter["n"])
    check("status max_hops", out.get("status") == "max_hops", out.get("status"))
    check("not ok", out["ok"] is False)

    print("\n[6] blocked stays blocked")
    async def wall(url, allow, force_browser=False):
        return {"final": url, "ok": False, "method": "http", "blocked": "vpn",
                "steps": [], "note": "VPN detected"}

    e._one_hop = wall
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("blocked status", out.get("status") == "blocked", out.get("status"))
    check("no false destination", out["ok"] is False)

    print("\n[7] structured hop logging")
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    lg = logging.getLogger("nickbot.resolver")
    h = Grab()
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    e._one_hop = fake_hop
    asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    lg.removeHandler(h)
    joined = "\n".join(records)
    check("logs hop numbers", "[resolver] hop=1" in joined and "[resolver] hop=2" in joined)
    check("logs the method", "[resolver] method=" in joined)
    check("logs intermediate=true", "intermediate=true" in joined)
    check("logs the final URL", f"[resolver] FINAL={FINAL}" in joined)

    print("\n[8] helpers")
    check("normalize adds a scheme", hops.normalize("vplink.in/x") == "https://vplink.in/x")
    check("normalize keeps the path", hops.normalize("HTTP://Vplink.in/nAKfh").endswith("/nAKfh"))
    check("host_of", hops.host_of(GATE) == "auth.pwasmultiverse.workers.dev")
    check("same_target ignores fragment", hops.same_target(FINAL + "#x", FINAL) is True)
    check("same_target catches a different page", hops.same_target(FINAL, GATE) is False)
    check("blocked text detected", "Human verification" in hops.looks_blocked("<div class=g-recaptcha>"))
    check("clean page not blocked", hops.looks_blocked("<p>hello</p>") == "")

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
