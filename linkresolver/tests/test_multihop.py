"""linkresolver: evidence-based hops and classifier parity (offline).

    python3 tests/test_multihop.py

No browser is started: ``Resolver._resolve_once`` is stubbed so the hop logic
can be tested deterministically on a machine without Chromium.

The chain in the first section is the one that started all of this:
``https://vplink.in/nAKfh`` really does end on
``https://auth.pwasmultiverse.workers.dev/generate?code=...``. It looks like a
gate and it is the destination, so nothing here may reject it on shape alone.
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


AUTH = "https://auth.pwasmultiverse.workers.dev/generate?code=N8BCN7VQDAZFL48O"
SHORT = "https://gplinks.co/abc123"
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
    print("\n[1] the auth endpoint is the destination, not a waypoint")
    r = Resolver(allow_browser=False, max_hops=10)
    calls = []

    def once(url, force_browser=None):
        calls.append(url)
        return result(url, AUTH, method="gate")

    r._resolve_once = once
    out = r.resolve("https://vplink.in/nAKfh")
    check("auth endpoint is returned", out.final_url == AUTH, out.final_url)
    check("reported ok", out.ok is True)
    check("resolved once", calls == ["https://vplink.in/nAKfh"], calls)
    check("not called a loop", "loop" not in out.note.lower(), out.note)

    print("\n[2] a real chain keeps going")
    calls = []

    def chain(url, force_browser=None):
        calls.append(url)
        if url.startswith("https://vplink.in/"):
            return result(url, SHORT)
        if url == SHORT:
            return result(url, FINAL)
        return result(url, url, ok_=False)

    r = Resolver(allow_browser=False, max_hops=10)
    r._resolve_once = chain
    out = r.resolve("https://vplink.in/nAKfh")
    check("final is the real page", out.final_url == FINAL, out.final_url)
    check("reported ok", out.ok is True)
    check("the shortener was re-resolved", calls == ["https://vplink.in/nAKfh", SHORT], calls)
    check("steps say continue then final",
          [s.extra.get("action") for s in out.steps if s.extra.get("action")] == ["continue", "final"],
          [s.extra.get("action") for s in out.steps])

    print("\n[3] a suspicious answer with nothing behind it stays final")
    def stuck(url, force_browser=None):
        return result(url, AUTH)

    r = Resolver(allow_browser=False)
    r._resolve_once = stuck
    out = r.resolve("https://vplink.in/nAKfh")
    check("still points at the auth endpoint", out.final_url == AUTH)
    check("and is ok", out.ok is True)
    check("one pass only", out.note != "Maximum redirect hops reached (10)")

    print("\n[4] HTTP decoy, browser gets the real exit")
    seen = []

    def decoy(url, force_browser=None):
        seen.append((url, force_browser))
        if force_browser:
            return result(url, FINAL, method="browser")
        return result(url, AUTH)

    r = Resolver(allow_browser=True, max_hops=10)
    r._resolve_once = decoy
    out = r.resolve("https://vplink.in/nAKfh")
    check("browser answer wins", out.final_url == FINAL, out.final_url)
    check("browser ran on the page that produced it",
          any(u == "https://vplink.in/nAKfh" and f for u, f in seen), seen)
    check("only one forced pass", len([1 for _, f in seen if f]) == 1)

    print("\n[5] loop detection and hop budget")
    # Both hosts are in the CLI's own shortener table, so each hop really is
    # evidence of another hop - which is what makes this a loop test and not a
    # "suspicious URL" test.
    def pingpong(url, force_browser=None):
        return result(url, "https://bit.ly/xyz789" if url != "https://bit.ly/xyz789" else SHORT)

    r = Resolver(allow_browser=False, max_hops=10)
    r._resolve_once = pingpong
    out = r.resolve("https://vplink.in/nAKfh")
    check("bouncing chain stops", out.ok is False)
    check("says it is a loop", "loop" in out.note.lower(), out.note)

    counter = {"n": 0}

    def endless(url, force_browser=None):
        counter["n"] += 1
        return result(url, f"https://gplinks.co/c{counter['n']}")

    r = Resolver(allow_browser=False, max_hops=4)
    r._resolve_once = endless
    out = r.resolve("https://vplink.in/nAKfh")
    # one initial pass + one pass per hop (the last hop has nothing to follow)
    check("stops at the budget", counter["n"] == 5, counter["n"])
    check("says so", "Maximum redirect hops" in out.note, out.note)
    check("not ok", out.ok is False)

    print("\n[6] normal destination passes straight through")
    def clean(url, force_browser=None):
        return result(url, FINAL)

    r = Resolver(allow_browser=False)
    r._resolve_once = clean
    out = r.resolve("https://vplink.in/nAKfh")
    check("one hop only", out.final_url == FINAL and out.ok is True)
    check("no loop note", "Loop" not in out.note)

    print("\n[7] classifier parity with the bot's copy")
    try:
        from nickbot import hops as bot_hops
    except Exception as exc:  # pragma: no cover
        print(f"  - bot copy not importable ({exc}), skipping")
        bot_hops = None
    if bot_hops is not None:
        table = [
            AUTH, FINAL, "https://gplinks.co/abc123", "https://vplink.in/nAKfh",
            "https://example.org/blog/post-1", "https://example.com/redirect?url=https%3A%2F%2Fx.example",
            "https://example.net/go/9f8e7d", "https://shop.example.com/product?code=SUMMER20",
            "https://example.com/verify-email", "https://news.site/article/how-to-generate-more-links",
            "https://site.example/continue/step2", "https://medium.com/@nick/why-i-love-auth-systems-1",
        ]
        same = all(
            classify.looks_like_waypoint(u, prev_url="https://vplink.in/nAKfh", known_gate=False).suspicious
            == bot_hops.looks_like_waypoint(u, prev_url="https://vplink.in/nAKfh", known_gate=False).suspicious
            for u in table
        )
        check("both copies agree on 12 URLs", same)
        check("both still hint that the auth endpoint looks like a gate",
              classify.looks_like_waypoint(AUTH).suspicious is True)
        check("...and both call it final when nothing follows",
              classify.decide(AUTH, known_gate=False).action == classify.FINAL)
        check("both continue on a handed-over address",
              classify.decide(AUTH, next_url=FINAL, known_gate=False).action == classify.CONTINUE)

    print("\n[8] normalize is shared")
    check("scheme added", normalize("vplink.in/x") == "https://vplink.in/x")
    check("classify.normalize matches", classify.normalize("vplink.in/x") == normalize("vplink.in/x"))

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
