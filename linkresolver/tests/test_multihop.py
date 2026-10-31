"""linkresolver: multi-hop chain and classifier parity (offline).

    python3 tests/test_multihop.py

No browser is started: ``Resolver._resolve_once`` is stubbed so the hop logic
can be tested deterministically on a machine without Chromium.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]      # .../linkresolver
REPO = ROOT.parent                                      # the repo root
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "nickbot"))

from linkresolver import classify                       # noqa: E402
from linkresolver.engine import Resolver, normalize     # noqa: E402
from linkresolver.models import Result, Step            # noqa: E402

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


def result(url, final, ok_=True, method="http"):
    r = Result(input_url=url)
    r.final_url = final
    r.ok = ok_
    r.method = method
    r.steps = [Step("request", url, status=200, detail="stub")]
    return r


def main() -> int:
    print("\n[1] a chain that ends on a generated auth endpoint keeps going")
    r = Resolver(allow_browser=False, max_hops=10)
    calls = []

    def once(url, force_browser=None):
        calls.append(url)
        if url.startswith("https://vplink.in/"):
            return result(url, GATE, method="browser")
        if url == GATE:
            return result(url, FINAL)
        return result(url, url, ok_=False)

    r._resolve_once = once
    out = r.resolve("https://vplink.in/nAKfh")
    check("final is the real page", out.final_url == FINAL, out.final_url)
    check("reported ok", out.ok is True)
    check("the intermediate URL was re-resolved", calls == ["https://vplink.in/nAKfh", GATE], calls)
    check("steps explain the hop", any("intermediate" in s.detail for s in out.steps))

    print("\n[2] a dead end is not sold as success")
    def stuck(url, force_browser=None):
        return result(url, GATE)

    r._resolve_once = stuck
    out = r.resolve("https://vplink.in/nAKfh")
    check("still points at the waypoint", out.final_url == GATE)
    check("but is not ok", out.ok is False)
    check("says it is a loop", "Loop" in out.note or "loop" in out.note, out.note)

    print("\n[3] hop budget")
    counter = {"n": 0}
    def endless(url, force_browser=None):
        counter["n"] += 1
        return result(url, f"https://auth{counter['n']}.workers.dev/generate?code=AB12CD34EF")

    r = Resolver(allow_browser=False, max_hops=4)
    r._resolve_once = endless
    out = r.resolve("https://vplink.in/nAKfh")
    check("stops at the budget", counter["n"] == 4, counter["n"])
    check("says so", "Maximum redirect hops" in out.note, out.note)
    check("not ok", out.ok is False)

    print("\n[4] normal destination passes straight through")
    def clean(url, force_browser=None):
        return result(url, FINAL)

    r = Resolver(allow_browser=False)
    r._resolve_once = clean
    out = r.resolve("https://vplink.in/nAKfh")
    check("one hop only", out.final_url == FINAL and out.ok is True)
    check("no loop note", "Loop" not in out.note)

    print("\n[5] classifier parity with the bot's copy")
    try:
        from nickbot import hops as bot_hops
    except Exception as exc:  # pragma: no cover
        print(f"  - bot copy not importable ({exc}), skipping")
        bot_hops = None
    if bot_hops is not None:
        table = [
            GATE, FINAL, "https://gplinks.co/abc123", "https://vplink.in/nAKfh",
            "https://example.org/blog/post-1", "https://example.com/redirect?url=https%3A%2F%2Fx.example",
            "https://example.net/go/9f8e7d", "https://shop.example.com/product?code=SUMMER20",
            "https://example.com/verify-email", "https://news.site/article/how-to-generate-more-links",
            "https://site.example/continue/step2", "https://medium.com/@nick/why-i-love-auth-systems-1",
        ]
        same = all(
            classify.is_intermediate(u, prev_url="https://vplink.in/nAKfh", known_gate=False).intermediate
            == bot_hops.is_intermediate(u, prev_url="https://vplink.in/nAKfh", known_gate=False).intermediate
            for u in table
        )
        check("both copies agree on 12 URLs", same)
        check("both flag the auth endpoint",
              classify.is_intermediate(GATE).intermediate is True)

    print("\n[6] normalize is shared")
    check("scheme added", normalize("vplink.in/x") == "https://vplink.in/x")
    check("classify.normalize matches", classify.normalize("vplink.in/x") == normalize("vplink.in/x"))

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
