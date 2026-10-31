"""Proxy farm (proxyfarm) offline tests.

Three things matter here:
1. Parsing: only public ip:port entries; RFC1918 addresses must be rejected
   (the 10.x.x.x:port list on that paste site is worthless);
2. The pool: add/remove/ranking/persistence, site-verified entries first;
3. Batch country lookup and the site-probe filter logic (fake data, no network).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nickbot.proxyfarm import Farm, norm_proxy, parse_lines  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


def t_parse():
    print("[1] URL parsing")
    check("bare ip:port", norm_proxy("103.88.237.97:82") == "http://103.88.237.97:82")
    check("http prefix", norm_proxy("http://45.65.226.174:999") == "http://45.65.226.174:999")
    check("socks5 prefix", norm_proxy("socks5://49.51.244.112:8888") == "socks5://49.51.244.112:8888")
    check("trailing punctuation removed", norm_proxy("1.2.3.4:8080,") == "http://1.2.3.4:8080")

    # Key point: the 10.x addresses in that paste-site screenshot are not proxies
    check("rejects 10.x private range", norm_proxy("10.6.20.1:62790") is None)
    check("rejects 192.168", norm_proxy("192.168.1.5:8080") is None)
    check("rejects 172.16", norm_proxy("172.16.4.4:3128") is None)
    check("rejects 127.0.0.1", norm_proxy("127.0.0.1:8080") is None)
    check("rejects junk port 443", norm_proxy("8.8.8.8:443") is None)
    check("rejects out-of-range IP", norm_proxy("300.1.1.1:8080") is None)
    check("rejects non-address text", norm_proxy("no proxy here") is None)

    lines = parse_lines("1.1.1.1:80\n\nbad\nsocks4://2.2.2.2:4145\n10.0.0.9:80\n")
    check("batch parse keeps only valid entries", lines == ["http://1.1.1.1:80", "socks4://2.2.2.2:4145"], lines)
    check("default scheme can be set", parse_lines("3.3.3.3:1080", "socks5") == ["socks5://3.3.3.3:1080"])


def t_pool():
    print("[2] pool behaviour")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "in.json"
        f = Farm(country="IN", path=p)

        f.add("1.1.1.1:8080")
        f.add("2.2.2.2:8080")
        f.add("10.0.0.1:8080")           # private range, must be rejected
        check("public addresses only", f.stats()["total"] == 2, f.stats())

        # simulate a site-verified entry with lower latency
        f._db["http://2.2.2.2:8080"].update({"site": True, "latency": 120})
        f._db["http://1.1.1.1:8080"].update({"site": False, "latency": 90})
        check("site-ok beats raw latency", f.sample(1) == ["http://2.2.2.2:8080"], f.sample(2))

        f.drop("http://1.1.1.1:8080")
        check("one failure only demotes", "http://1.1.1.1:8080" in f._db)
        f.drop("http://1.1.1.1:8080")
        check("dropped only after two failures", "http://1.1.1.1:8080" not in f._db)

        check("remove works", f.remove("2.2.2.2:8080") is True)
        check("removing a missing entry returns False", f.remove("9.9.9.9:1") is False)

        # persistence
        f.add("5.5.5.5:8080")
        f2 = Farm(country="IN", path=p)
        check("survives a restart", "http://5.5.5.5:8080" in f2._db, json.dumps(f2._db))
        check("pool file is valid JSON", isinstance(json.loads(p.read_text()), dict))

        n = f.clear()
        check("clear returns a count", n >= 1)
        check("empty after clear", f.stats()["total"] == 0)


def t_geo_filter():
    print("[3] country filter and site probe")
    with tempfile.TemporaryDirectory() as d:
        f = Farm(country="IN", path=Path(d) / "in.json")
        f._db = {
            "http://1.1.1.1:80": {"ok": True, "cc": "IN", "latency": 100, "checked": 9e9},
            "http://2.2.2.2:80": {"ok": True, "cc": "US", "latency": 50, "checked": 9e9},
            "http://3.3.3.3:80": {"ok": False, "cc": "IN", "latency": 10, "checked": 9e9},
        }
        ranked = f._ranked()
        check("only same-country, ok entries", ranked == ["http://1.1.1.1:80"], ranked)

        # Site probe: a fake transport is awkward here, so test the wall check directly
        async def probe():
            return await f._probe_site("http://127.0.0.1:1", "http://127.0.0.1:1/x", 1.0)

        check("unreachable = unusable", asyncio.run(probe()) is False)


def main():
    t_parse()
    t_pool()
    t_geo_filter()
    print(f"\nResult: {ok} passed / {fail} failed")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
