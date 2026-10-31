"""CLI entry point: python -m linkresolver <url> [<url> ...]"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from .engine import Resolver
from .models import Result

C = {
    "dim": "\033[2m", "reset": "\033[0m", "bold": "\033[1m",
    "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
    "cyan": "\033[36m", "mag": "\033[35m", "gray": "\033[90m",
}


def _c(text: str, color: str) -> str:
    return f"{C[color]}{text}{C['reset']}"


KIND_STYLE = {
    "request": ("cyan", "GET "),
    "redirect": ("mag", "→   "),
    "extract": ("green", "EXTRACT"),
    "click": ("yellow", "CLICK"),
    "note": ("gray", "NOTE"),
    "blocked": ("red", "BLOCKED"),
    "error": ("red", "ERROR"),
}


def render(res: Result) -> str:
    out: List[str] = []
    out.append("")
    out.append(_c(f"input   {res.input_url}", "dim"))
    out.append("")

    for i, s in enumerate(res.steps, 1):
        color, tag = KIND_STYLE.get(s.kind, ("gray", s.kind))
        status = f" {s.status}" if s.status else ""
        elapsed = f" {s.elapsed:.2f}s" if s.elapsed else ""
        url = s.url if len(s.url) <= 96 else s.url[:93] + "..."
        out.append(f"{_c(f'{i:>2}.', 'gray')} {_c(tag, color)} {url}{_c(status + elapsed, 'gray')}")
        if s.detail:
            out.append(f"    {_c(s.detail, 'dim')}")

    out.append("")
    if res.ok:
        out.append(_c("  OK  destination", "green") + f"  {res.final_url}")
    else:
        out.append(_c("  --  no destination", "red") + f"  {res.note or 'unknown reason'}")
        if res.final_url and res.final_url != res.input_url:
            out.append(_c(f"    last address: {res.final_url}", "dim"))
    out.append(_c(f"  method {res.method or '-'} · took {res.elapsed:.2f}s", "dim"))
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="linkresolver",
        description="Resolve short links and ad gates to the real destination (HTTP fast path + Chromium fallback)",
    )
    ap.add_argument("urls", nargs="+", help="one or more short links")
    ap.add_argument("--browser", action="store_true", help="force browser mode")
    ap.add_argument("--no-browser", action="store_true", help="no browser fallback, HTTP only")
    ap.add_argument("--headful", action="store_true", help="show the browser window (debugging)")
    ap.add_argument("--timeout", type=float, default=25.0, help="per-site timeout in seconds (default 25)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args(argv)

    allow_browser = not args.no_browser
    results = []
    for u in args.urls:
        res = Resolver(
            timeout=args.timeout,
            allow_browser=allow_browser or args.browser,
            force_browser=args.browser,
            headless=not args.headful,
            browser_timeout=max(args.timeout, 30.0),
        ).resolve(u)
        results.append(res)
        if not args.json:
            print(render(res))

    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
