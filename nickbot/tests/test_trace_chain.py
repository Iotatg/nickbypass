"""The chain line must show navigations, not noise.

The live trace of ``vplink.in/nAKfh`` produced this CHAIN:

    gate --initialFrameNavigation--> about:blank
         --scriptInitiated--> pipefitter/studyeducations
         --http302--> vplink.in/cdn-cgi/challenge-platform/.../main.js
         --initialFrameNavigation--> about:blank
         --scriptInitiated--> pipefitter/article

Two of those five edges are not navigations at all: ``about:blank`` is the
empty frame the ad-blocker leaves behind, and the ``http302`` is a *script*
request inside Cloudflare's challenge, not a document. Reading that line, the
gate looks like it bounces through Cloudflare on its way to the article - it
does not. A chain line that is wrong is worse than no chain line, so the
filter is pinned here.

Pure data: no browser, no network, no Selenium.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.trace_live import SeleniumHooks          # noqa: E402

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


def hooks(events):
    h = SeleniumHooks()
    h.net = events
    return h


GATE = "https://vplink.in/nAKfh"
LAND = ("https://pipefitter.in/studyinsurances/studyeducations/"
        "?insurancesstudy=nAKfh&uiso=12608")
ART = "https://pipefitter.in/studyinsurances/best-premium-credit-cards/"


def live_events():
    """The devtools events from the real phone run, trimmed to the essentials."""
    return [
        {"kind": "req", "t": 79, "url": GATE, "type": "Document",
         "status": None, "frm": "", "initiator": "other"},
        {"kind": "resp", "t": 2189, "url": GATE, "status": 200, "loc": "",
         "type": "Document"},
        {"kind": "nav-commit", "t": 2189, "url": GATE},
        # the ad-blocker's empty frames - never part of the chain
        {"kind": "nav-req", "t": 2219, "url": "about:blank",
         "reason": "initialFrameNavigation", "disposition": "currentTab"},
        {"kind": "nav-sched", "t": 2715, "url": LAND,
         "reason": "scriptInitiated", "delay": 0},
        {"kind": "nav-req", "t": 2715, "url": LAND,
         "reason": "scriptInitiated", "disposition": "currentTab"},
        {"kind": "req", "t": 2741, "url": LAND, "type": "Document",
         "status": None, "frm": "", "initiator": "script"},
        # a 302 on a *script* is not a navigation
        {"kind": "req", "t": 3706,
         "url": "https://vplink.in/cdn-cgi/challenge-platform/h/g/scripts/jsd/x/main.js",
         "type": "Script", "status": 302,
         "frm": "https://vplink.in/cdn-cgi/challenge-platform/scripts/jsd/main",
         "initiator": "other"},
        {"kind": "nav-req", "t": 4395, "url": "about:blank",
         "reason": "initialFrameNavigation", "disposition": "currentTab"},
        {"kind": "resp", "t": 5614, "url": LAND, "status": 200, "loc": "",
         "type": "Document"},
        {"kind": "nav-commit", "t": 5614, "url": LAND},
        {"kind": "nav-req", "t": 5628, "url": ART,
         "reason": "scriptInitiated", "disposition": "currentTab"},
        {"kind": "req", "t": 5676, "url": ART, "type": "Document",
         "status": None, "frm": "", "initiator": "script"},
        {"kind": "resp", "t": 7972, "url": ART, "status": 200, "loc": "",
         "type": "Document"},
        {"kind": "nav-commit", "t": 7972, "url": ART},
    ]


def main() -> int:
    print("chain: the real live event stream")
    h = hooks(live_events())
    edges = h.chain(GATE)
    line = h.chain_line(GATE, ART)

    check("two edges, not five", len(edges) == 2, line)
    check("no about:blank", "about:blank" not in line, line)
    check("no subresource 302", "main.js" not in line, line)
    check("gate -> landing", edges[0]["to"] == LAND, edges)
    check("landing -> article", edges[1]["to"] == ART, edges)
    check("both edges are script navigations",
          [e["how"] for e in edges] == ["scriptInitiated", "scriptInitiated"], edges)

    print("mechanism: gate -> landing -> article")
    mech, detail = h.mechanism(ART, GATE)
    check("D, not A (there is no 302 from the gate)",
          mech == "D", f"{mech}: {detail}")
    check("the detail names the remaining hops", "more hop" in detail, detail)

    print("mechanism: a gate that really answers 302")
    h2 = hooks([
        {"kind": "req", "t": 10, "url": GATE, "type": "Document",
         "status": 302, "frm": "https://vplink.in/go/1", "initiator": "other"},
        {"kind": "nav-req", "t": 11, "url": ART,
         "reason": "scriptInitiated", "disposition": "currentTab"},
    ])
    mech2, detail2 = h2.mechanism(ART, GATE)
    check("A when the gate itself answers 3xx", mech2 == "A", f"{mech2}: {detail2}")

    print("mechanism: meta refresh")
    h3 = hooks([{"kind": "nav-req", "t": 5, "url": ART,
                 "reason": "metaTagRefresh", "disposition": "currentTab"}])
    mech3, _ = h3.mechanism(ART, GATE)
    check("C for a meta refresh", mech3 == "C", mech3)

    print("no edges at all")
    h4 = hooks([])
    check("empty chain -> F", h4.mechanism(ART, GATE)[0] == "F")
    check("empty chain line is honest", "no navigation" in h4.chain_line(GATE, ART))

    print(f"\nResult: {ok} passed / {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
