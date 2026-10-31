#!/usr/bin/env python3
"""Rebuild ``nickbot/domains.txt`` from public shortener lists.

The registry has two layers:

* ``nickbot/registry.py`` — hand-written, ~180 domains, each with a real name
  and a strategy (``gt`` / ``gate`` / ``short``).  Edited by a human.
* ``nickbot/domains.txt`` — this script's output: every other domain
  we can recognise as a shortener, so the bot can name it and refuse to hand
  an ad page back as the "destination".  Regenerated, never hand-edited.

Sources (all public, all fetched at build time):

1. HaGeZi DNS blocklist "urlshortener" — the broad catalogue. A domain only
   lands here if it *is* a shortener, which keeps the list precise.
2. uBlock Origin "Link shorteners" filter list — used only as a second
   opinion. That list also mentions ordinary sites that merely *host* a gate
   (a blogspot page, an APK blog), so a domain is taken from it only when it
   looks like a shortener by name or is already in the HaGeZi catalogue.

FastForward's bypass rules were evaluated and left out on purpose: its
``matches`` arrays are mostly file hosts and content sites, not shorteners.

Every candidate is folded to its registrable domain, DNS-checked once, and
dropped if it does not resolve.  Dead shorteners are noise: the bot would
otherwise claim support for sites that no longer exist.

Usage::

    python3 tools/build_sites.py            # full rebuild
    python3 tools/build_sites.py --no-dns   # keep unresolvable domains too
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "nickbot" / "domains.txt"

UA = {"User-Agent": "nickbot-registry/1.0 (+https://github.com/Iotatg/nickbypass)"}

UBO = ("https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/"
       "filters/ubo-link-shorteners.txt")
HAGEZI = ("https://raw.githubusercontent.com/hagezi/dns-blocklists/main/"
          "wildcard/urlshortener-onlydomains.txt")

HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
TLD_RE = re.compile(r"^[a-z]{2,24}$")
TWO_LEVEL = {"co.in", "co.uk", "com.au", "co.nz", "com.br", "co.id", "com.pk",
             "com.bd", "com.np", "co.za", "com.ng", "com.my", "co.ke", "com.tr"}

# 不是短链站，别塞进来：文件托管 / 大厂 / 广告网络 / 学校政府
DENY = (
    "blogspot.", "google.", "googleusercontent.", "gstatic.", "cloudflare.",
    "cloudfront.", "a-ads.com", "doubleclick.", "googlesyndication.",
    "facebook.", "fbcdn.", "instagram.", "twitter.", "telegram.", "whatsapp.",
    "youtube.", "youtu.be", "wikipedia.", "github.", "gitlab.", "amazon.",
    "flipkart.", "mega.nz", "mediafire.", "dropbox.", "sourceforge.net",
    "4shared.com", "filecrypt.", "1fichier.", "akirabox.", "1024tera.",
    "terabox.", "krakenfiles.", "1cloudfile.", "4funbox.", "curseforge.",
    "softpedia.", "portableapps.", "wetransfer.", "oracle.", "quantcast.",
    "id5-sync.", "yastatic.", "streamelements.", "mydramalist.", "rentry.co",
)
BAD_SUFFIX = (".ac.uk", ".edu", ".gov", ".mil", ".edu.in", ".ac.in", ".gov.in",
              ".nic.in", ".res.in", ".sch.uk", ".edu.au", ".ac.nz")

HINT = ("link", "short", "url", "clk", "click", "earn", "cut", "shrink",
        "adf", "drop", "fly", "boost", "ouo", "tii", "oii", "tpi", "mdisk",
        "loot", "srt", "unlock", "sub2", "go.")


def get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fold(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def valid(host: str) -> bool:
    if not host or len(host) > 253 or ".." in host:
        return False
    if not HOST_RE.match(host):
        return False
    if not TLD_RE.match(host.rsplit(".", 1)[-1]):
        return False
    if any(d in host for d in DENY) or host.endswith(BAD_SUFFIX):
        return False
    return True


def dns_ok(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except Exception:
        return False


def from_ubo() -> set[str]:
    text = get(UBO).decode("utf-8", "ignore")
    out: set[str] = set()
    for m in re.finditer(r"domain=([^\s,$]+)", text):
        for d in m.group(1).split("|"):
            out.add(d.strip().lower().lstrip("~"))
    for m in re.finditer(r"^\|\|([a-z0-9.*-]+)\^", text, re.M):
        out.add(m.group(1).lower())
    for line in text.splitlines():
        if "##" not in line or line.lstrip().startswith("!"):
            continue
        left = line.split("#")[0].strip()
        if not left or "=" in left:
            continue
        for d in left.split(","):
            out.add(d.strip().lower())
    return {d for d in out if d and "*" not in d}


def from_hagezi() -> set[str]:
    text = get(HAGEZI).decode("utf-8", "ignore")
    out = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if not line or line.startswith("#") or line.count(".") != 1:
            continue
        if any(k in line for k in HINT):
            out.add(line)
    return out


def from_fastforward() -> set[str]:
    """Kept for reference — not used to build the list.

    FastForward bypasses file hosts and content sites as often as they bypass
    shorteners, so folding its ``matches`` arrays in made the registry claim
    sites like ``apkhubs.com`` were shorteners.  The domains worth keeping
    from it are hand-written in ``registry.CURATED`` instead.
    """
    return set()


def curated_domains() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from nickbot.registry import CURATED  # noqa: E402

    return {d for d, _, _ in CURATED}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-dns", action="store_true", help="keep domains that do not resolve")
    ap.add_argument("--limit", type=int, default=0, help="cap the output size")
    args = ap.parse_args()

    pools = {}
    for name, fn in (("HaGeZi", from_hagezi), ("uBlock", from_ubo)):
        try:
            pools[name] = fn()
            print(f"{name:<12} {len(pools[name]):>5} raw")
        except Exception as exc:
            print(f"{name:<12} FAILED: {type(exc).__name__}: {exc}")
            pools[name] = set()

    catalogue = {fold(h) for h in pools.get("HaGeZi", set())}
    cand = set(catalogue)
    for host in pools.get("uBlock", set()):
        # uBO also names sites that merely host a gate — only trust it when the
        # domain is already in the catalogue or its name says "shortener"
        if fold(host) in catalogue or any(k in host for k in HINT):
            cand.add(fold(host))
    cand = {h for h in cand if valid(h)}
    cand -= curated_domains()
    print(f"{'candidates':<12} {len(cand):>5} after fold/filter")

    if not args.no_dns:
        hosts = sorted(cand)
        with ThreadPoolExecutor(max_workers=100) as ex:
            alive = [h for h, ok in zip(hosts, ex.map(dns_ok, hosts)) if ok]
        print(f"{'alive':<12} {len(alive):>5} resolved")
        cand = set(alive)

    if args.limit:
        cand = set(sorted(cand)[: args.limit])

    rows = sorted(cand)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = [
        "# nickbot shortener registry — generated file, do not hand-edit.",
        f"# rebuilt {stamp} by tools/build_sites.py",
        "# sources: uBlock Origin link-shorteners · HaGeZi urlshortener · FastForward",
        f"# entries: {len(rows)}  (curated domains live in nickbot/registry.py)",
        "#",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(header + rows) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(rows)} domains, {OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
