"""Waypoint hints, the evidence-based decision, and the multi-hop loop (offline).

Two things are locked down here, and they pull in opposite directions:

1. ``auth.pwasmultiverse.workers.dev/generate?code=...`` **is the real
   destination** of ``https://vplink.in/nAKfh``. It looks like a gate, it scores
   like a gate, and rejecting it on sight made the bot walk past the answer the
   user actually wanted. A URL's name is never evidence.
2. A gate really does hide its exit from HTTP clients. When an answer merely
   *looks* like a waypoint, one browser pass on the page that produced it is
   worth the time - and if the browser finds nothing either, the answer stands.

So: ``looks_like_waypoint`` is a hint, ``decide`` is the verdict, and only the
latter is allowed to end or continue a chain.
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


AUTH = "https://auth.pwasmultiverse.workers.dev/generate?code=N8BCN7VQDAZFL48O"
SHORT = "https://gplinks.co/abc123"
FINAL = ("https://targetssc.com/studyinsurances/"
         "best-short-term-health-insurance-in-usa-2026/")


def hint(url, **kw):
    return hops.looks_like_waypoint(url, **kw)


def decide(url, **kw):
    return hops.decide(url, **kw)


def main() -> int:
    print("\n[1] the hint still recognises waypoint-shaped URLs")
    check("generated auth endpoint scores high", hint(AUTH).suspicious is True)
    check("...and says why", "gate path" in hint(AUTH).why or "workers.dev" in hint(AUTH).why)
    check("known shortener", hint("https://gplinks.co/abc123").suspicious is True)
    check("known gate", hint("https://vplink.in/nAKfh").suspicious is True)
    check("open redirector", hint(
        "https://example.com/redirect?url=https%3A%2F%2Fevil.example%2Fx").suspicious is True)
    check("/go/ hop", hint("https://example.net/go/9f8e7d").suspicious is True)
    check("/verify with token", hint(
        "https://site.example/verify?token=AB12cd34EF56").suspicious is True)
    check("continue page", hint("https://site.example/continue/step2").suspicious is True)
    check("same host as previous hop tips a weak signal over",
          hint("https://site.example/generate", prev_url="https://site.example/x").suspicious is True)
    check("page text can tip it", hint(
        "https://site.example/p", text="Please wait... redirecting you in 5 seconds").suspicious is True)

    print("\n[2] the hint leaves real destinations alone")
    check("the real target", hint(FINAL).suspicious is False)
    check("article slug", hint("https://example.org/blog/post-1").suspicious is False)
    check("article about generating links", hint(
        "https://news.site/article/how-to-generate-more-links").suspicious is False)
    check("auth in a slug", hint(
        "https://medium.com/@nick/why-i-love-auth-systems-1234abcd").suspicious is False)
    check("shop coupon code", hint(
        "https://shop.example.com/product?code=SUMMER20").suspicious is False)
    check("verify-email page", hint("https://example.com/verify-email").suspicious is False)
    check("deep path on a shortener host", hint(
        "https://shrtfly.com/blog/some-post").suspicious is False)
    check("plain page", hint("https://example.com/about").suspicious is False)
    check("content page with utm tags", hint(
        "https://site.example/blog/how-to-do-x?utm_source=tg").suspicious is False)
    check("article title down-weights", hint(
        "https://site.example/generate", title="How to generate a strong password safely").suspicious is False)

    print("\n[3] decide(): the hint alone never ends a chain")
    check("suspicious URL with nothing behind it is FINAL", decide(AUTH).action == hops.FINAL)
    check("...and keeps the URL", decide(AUTH).url == AUTH)
    check("...and says why it is not rejected", "looks like a waypoint" in decide(AUTH).reason)
    check("high score does not mean reject", hint(AUTH).score >= 5)
    check("a handed-over address continues the chain",
          decide(AUTH, next_url=FINAL).action == hops.CONTINUE)
    check("...and becomes the next hop", decide(AUTH, next_url=FINAL).url == FINAL)
    check("the same address is not a next hop",
          decide(AUTH, next_url=AUTH + "#x").action == hops.FINAL)
    check("a shortener host continues (it exists to forward)",
          decide(SHORT, prev_url="https://vplink.in/x", known_gate=True).action == hops.CONTINUE)
    check("...but not when it is already the page we are on",
          decide(SHORT, prev_url=SHORT, known_gate=True).action == hops.FINAL)
    check("a normal destination is final", decide(FINAL, known_gate=False).action == hops.FINAL)
    check("plain page is final", decide("https://example.com/about", known_gate=False).action == hops.FINAL)

    print("\n[4] hop loop: shortener -> shortener -> final")
    cfg = Config.load()
    cfg.allow_browser = True
    cfg.max_hops = 10
    e = Engine(cfg)

    calls = []

    async def fake_hop(url, allow, force_browser=False):
        calls.append((url, force_browser))
        if url.startswith("https://vplink.in/"):
            return {"final": SHORT, "ok": True, "method": "http", "steps": [], "note": ""}
        if url == SHORT:
            return {"final": FINAL, "ok": True, "method": "http", "steps": [], "note": ""}
        return {"final": url, "ok": False, "method": "http", "steps": [], "note": "?"}

    e._one_hop = fake_hop
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("final is the real destination", out["final"] == FINAL, out["final"])
    check("marked ok", out["ok"] is True)
    check("status ok", out.get("status") == "ok")
    check("two hops recorded", len(out["hops"]) == 2, out.get("hops"))
    check("first hop continued", out["hops"][0]["action"] == "continue")
    check("second hop was final", out["hops"][1]["action"] == "final")
    chain = [u for u, forced in calls if not forced]
    check("both URLs were actually resolved", chain[:2] == ["https://vplink.in/nAKfh", SHORT], chain)

    print("\n[5] the user's case: the auth endpoint IS the destination")
    async def vplink_only(url, allow, force_browser=False):
        return {"final": AUTH, "ok": True, "method": "gate", "steps": [], "note": ""}

    e._one_hop = vplink_only
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("auth endpoint returned as final", out["final"] == AUTH, out["final"])
    check("reported ok, not unresolved", out["ok"] is True)
    check("status ok", out.get("status") == "ok")
    check("single hop", len(out["hops"]) == 1, out.get("hops"))
    check("not marked as a loop", out.get("status") != "loop")

    print("\n[5b] an auth endpoint is a destination, and nothing is navigated from it")
    check("the vplink destination is one", hops.is_stop_endpoint(AUTH) is True)
    check("same shape, a different domain", hops.is_stop_endpoint(
        "https://auth.otherkey.workers.dev/generate?code=ZZ99XX88YY") is True)
    check("key endpoint", hops.is_stop_endpoint("https://key.example.com/activate?token=ab12cd34ef") is True)
    check("no credential, not an endpoint", hops.is_stop_endpoint("https://example.com/generate") is False)
    check("no auth shape, not an endpoint", hops.is_stop_endpoint(
        "https://shop.example.com/product?code=SUMMER20") is False)
    check("verify page is not one", hops.is_stop_endpoint(
        "https://example.com/verify?token=AB12cd34EF56") is False)
    check("the destination behind it is not one", hops.is_stop_endpoint(FINAL) is False)

    # The chain reaches it and must not spend a browser pass, because a browser
    # would follow the page's own "continue" link on to the ad layer.
    calls = []

    async def auth_hop(url, allow, force_browser=False):
        calls.append((url, force_browser))
        return {"final": AUTH, "ok": True, "method": "gate", "steps": [], "note": ""}

    e._one_hop = auth_hop
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("returned as the final URL", out["final"] == AUTH, out["final"])
    check("marked ok", out["ok"] is True)
    check("status ok", out.get("status") == "ok")
    check("one hop", len(out["hops"]) == 1, out.get("hops"))
    check("no browser pass on it", not any(f for _, f in calls), calls)

    # Even a forced browser run must not fetch it.
    class Boom:
        async def resolve(self, url):
            raise AssertionError(f"fetched {url}")

    e.fast = Boom()
    out = asyncio.run(Engine._one_hop(e, AUTH, True, force_browser=True))
    check("_one_hop never fetches it", out["final"] == AUTH and out["ok"] is True, out)
    check("method is endpoint", out["method"] == "endpoint", out.get("method"))

    print("\n[5c] the browser drives *through* the auth endpoint")
    check("first_stop_endpoint reads a dict trail",
          hops.first_stop_endpoint([{"kind": "redirect", "url": "https://vplink.in/x"},
                                    {"kind": "redirect", "url": AUTH}]) == AUTH)
    check("...ignores a clean trail",
          hops.first_stop_endpoint([{"kind": "redirect", "url": FINAL}]) == "")
    check("...and an empty one", hops.first_stop_endpoint([]) == "")

    AD = "https://pipefitter.in/studyinsurances/best-cancer-treatment-hospitals/"
    trail = [
        {"kind": "redirect", "url": "https://vplink.in/nAKfh", "detail": "In-browser navigation"},
        {"kind": "redirect", "url": AUTH, "detail": "In-browser navigation"},
        {"kind": "redirect", "url": AD, "detail": "In-browser navigation"},
    ]

    class FastSaysBrowser:
        """The gate API refused, so the browser runs - the live vplink case."""

        async def resolve(self, url):
            return {"input": url, "final": "", "ok": False, "method": "http",
                    "steps": [], "note": "Still on the shortener page",
                    "blocked": "", "needs_browser": True}

    e.fast = FastSaysBrowser()
    e._browser = lambda url: {"final": AD, "ok": True, "steps": trail,
                              "note": "Reached the destination in browser mode"}
    out = asyncio.run(Engine._one_hop(e, "https://vplink.in/nAKfh", True))
    check("the page it drove through wins", out["final"] == AUTH, out["final"])
    check("not the ad page it ended on", "pipefitter" not in out["final"], out["final"])
    check("marked ok", out["ok"] is True)
    check("says why", "auth endpoint" in out["note"], out["note"])

    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("the whole chain reports the auth endpoint", out["final"] == AUTH, out["final"])
    check("status ok", out.get("status") == "ok", out.get("status"))

    print("\n[6] HTTP decoy, browser gets the real exit (other link types)")
    # A /go/ waypoint, not an auth endpoint: this is the case where the browser
    # is still worth spending, because the gate feeds HTTP a decoy.
    decoy_url = "https://gate.example.net/go/9f8e7d"
    seen_calls = []

    async def decoy(url, allow, force_browser=False):
        seen_calls.append((url, force_browser))
        if force_browser:
            return {"final": FINAL, "ok": True, "method": "browser", "steps": [], "note": ""}
        return {"final": decoy_url, "ok": True, "method": "http", "steps": [], "note": ""}

    e._one_hop = decoy
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("browser answer wins", out["final"] == FINAL, out["final"])
    check("marked ok", out["ok"] is True)
    check("one hop, not two", len(out["hops"]) == 1, len(out["hops"]))
    check("browser was retried on the gate page",
          any(u == "https://vplink.in/nAKfh" and f for u, f in seen_calls))
    check("browser ran exactly once", len([1 for _, f in seen_calls if f]) == 1)

    print("\n[7] loop detection and hop budget")
    async def pingpong(url, allow, force_browser=False):
        nxt = SHORT if url != SHORT else "https://vplink.in/nAKfh"
        return {"final": nxt, "ok": True, "method": "http", "steps": [], "note": ""}

    e._one_hop = pingpong
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("bouncing chain stops", out["ok"] is False)
    check("stops quickly", len(out["hops"]) <= 3, len(out["hops"]))
    check("says loop", out.get("status") == "loop", out.get("status"))

    cfg2 = Config.load()
    cfg2.allow_browser = False
    cfg2.max_hops = 3
    e2 = Engine(cfg2)
    counter = {"n": 0}

    async def endless(url, allow, force_browser=False):
        counter["n"] += 1
        return {"final": f"https://gplinks.co/c{counter['n']}", "ok": True,
                "method": "http", "steps": [], "note": ""}

    e2._one_hop = endless
    out = asyncio.run(e2._resolve_inner("https://vplink.in/nAKfh", False))
    check("hop budget enforced", counter["n"] == 3, counter["n"])
    check("status max_hops", out.get("status") == "max_hops", out.get("status"))
    check("not ok", out["ok"] is False)

    print("\n[8] blocked stays blocked")
    async def wall(url, allow, force_browser=False):
        return {"final": url, "ok": False, "method": "http", "blocked": "vpn",
                "steps": [], "note": "VPN detected"}

    e._one_hop = wall
    out = asyncio.run(e._resolve_inner("https://vplink.in/nAKfh", True))
    check("blocked status", out.get("status") == "blocked", out.get("status"))
    check("no false destination", out["ok"] is False)

    print("\n[9] structured hop logging")
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
    check("logs the decision", "action=" in joined)
    check("logs why", "why=" in joined)
    check("logs the final URL", f"[resolver] FINAL={FINAL}" in joined)

    print("\n[10] helpers")
    check("normalize adds a scheme", hops.normalize("vplink.in/x") == "https://vplink.in/x")
    check("normalize keeps the path", hops.normalize("HTTP://Vplink.in/nAKfh").endswith("/nAKfh"))
    check("host_of", hops.host_of(AUTH) == "auth.pwasmultiverse.workers.dev")
    check("same_target ignores fragment", hops.same_target(FINAL + "#x", FINAL) is True)
    check("same_target catches a different page", hops.same_target(FINAL, AUTH) is False)
    check("blocked text detected", "Human verification" in hops.looks_blocked("<div class=g-recaptcha>"))
    check("clean page not blocked", hops.looks_blocked("<p>hello</p>") == "")

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
