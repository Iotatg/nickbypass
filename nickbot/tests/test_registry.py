"""Site registry tests: counts, duplicates, match rules, performance.

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


print("\n[1] scale")
st = sites.stats()
check("total > 500", st["total"] > 500, True)
check("total > 2000", st["total"] > 2000, True)
check("150+ curated entries", st["curated"] >= 150, True)
check("1000+ generated entries", st["listed"] >= 1000, True)
check("100+ hand-written gate/gt entries", st["gates"] >= 100, True)
check("40+ of them are gt", st["gt"] >= 40, True)
check("30+ plain redirectors", st["short"] >= 30, True)
print(f"      total={st['total']} curated={st['curated']} listed={st['listed']}")

print("\n[2] data hygiene")
curated = [d for d, _, _ in registry.CURATED]
check("no duplicates in the curated layer", len(curated), len(set(curated)))
check("the two layers do not overlap", len(set(curated) & set(sites.LISTED)), 0)
check("no duplicates in the generated layer", len(sites.LISTED), len(set(sites.LISTED)))
HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
bad = [d for d in sites.LISTED if not HOST.match(d)]
check("generated layer is all valid domains", bad, [])
bad = [d for d in curated if not HOST.match(d)]
check("curated layer is all valid domains", bad, [])
bad = [d for d in curated + list(sites.LISTED)
       if d.startswith(("10.", "127.", "192.168.", "172.")) or ".." in d or "*" in d]
check("no private ranges or wildcards", bad, [])
check("no scheme prefix", [d for d in sites.LISTED if "://" in d], [])
check("all lowercase", [d for d in curated + list(sites.LISTED) if d != d.lower()], [])
check("only four kinds", {k for _, _, k in registry.CURATED}, {"gt", "gate", "short"})

print("\n[3] match rules")
check("exact hit", sites.display_name("https://gplinks.co/abc"), "GPLinks")
check("suffix swap still matches", sites.display_name("https://gplinks.xyz/abc"), "GPLinks")
check("subdomain match", sites.display_name("https://go.gplinks.co/abc"), "GPLinks")
check("subdomain match 2", sites.display_name("https://drop.carbikenation.com/x"), "Drop (Carbike Nation)")
check("unknown domain returned as-is", sites.display_name("https://random-site.xyz/a"), "random-site.xyz")
check("empty string does not crash", sites.display_name(""), "unknown")
check("junk input does not crash", sites.display_name("not a url"), "unknown")
check("case insensitive", sites.display_name("https://GPlinks.CO/x"), "GPLinks")
check("generated layer recognised", sites.lookup("https://shrtfly.com/abc").kind, sites.KIND_LISTED)
check("generated layer name = domain", sites.display_name("https://shrtfly.com/abc"), "shrtfly.com")
check("t.co is not stolen by net.com", sites.lookup("https://net.com/x"), None)
check("bit.ly matches short", sites.lookup("https://bit.ly/x").kind, "short")
check("gt family recognised", sites.lookup("https://tii.la/x").kind, "gt")

print("\n[4] browser or not")
check("short needs no browser", sites.needs_browser("https://bit.ly/x"), False)
check("gate needs a browser", sites.needs_browser("https://adf.ly/x"), True)
check("gt needs a browser", sites.needs_browser("https://gplinks.co/x"), True)
check("generated layer, unsure = yes", sites.needs_browser("https://shrtfly.com/x"), True)
check("unknown domain = yes", sites.needs_browser("https://random-site.xyz/x"), True)

print("\n[5] destination check (an ad page is not a result)")
check("gate short code is not a destination", sites.is_gate("https://gplinks.co/abc123"), True)
check("generated layer short code is not a destination", sites.is_gate("https://shrtfly.com/abc123"), True)
check("generated layer deep path is a destination", sites.is_gate("https://shrtfly.com/blog/some-post"), False)
check("generated layer root path is a destination", sites.is_gate("https://shrtfly.com/"), False)
check("short code is not a destination either", sites.is_gate("https://bit.ly/abc"), True)
check("short deep path counts as a destination", sites.is_gate("https://bit.ly/blog/post-1"), False)
check("unknown domain counts as a destination", sites.is_gate("https://example.org/abc"), False)

print("\n[6] copy")
from html.parser import HTMLParser   # noqa: E402


class Balance(HTMLParser):
    """Telegram only accepts well-formed tags, so count them."""

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
check("has a total", "sites on the list" in txt, True)
check("length under control", len(txt) <= 4000, True)
check("tags closed", balanced(txt), True)
check("no unescaped &", "&" in txt.replace("&amp;", "").replace("&lt;", "").replace("&gt;", ""), False)
check("search hit", "GPLinks" in sites.search_text("gplinks"), True)
check("search output tags closed", balanced(sites.search_text("gplinks")), True)
check("search by domain", "shrtfly.com" in sites.search_text("shrtfly"), True)
check("an empty search explains itself", "generic engine" in sites.search_text("zzzz-nope"), True)
check("empty query = full list", sites.search_text(""), sites.supported_text())

print("\n[7] performance")
t = time.perf_counter()
for _ in range(2000):
    sites.lookup("https://gplinks.co/abc")
dt = time.perf_counter() - t
check("2000 hits < 0.5s", dt < 0.5, True)
t = time.perf_counter()
for _ in range(2000):
    sites.lookup("https://unknown-domain-xyz.com/a")
dt = time.perf_counter() - t
check("2000 misses < 0.5s", dt < 0.5, True)

print(f"\nResult: {PASS} passed / {FAIL} failed")
sys.exit(1 if FAIL else 0)
