"""命令行入口：python -m linkresolver <url> [<url> ...]"""

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
    "extract": ("green", "解出"),
    "click": ("yellow", "点击"),
    "note": ("gray", "备注"),
    "blocked": ("red", "拦截"),
    "error": ("red", "错误"),
}


def render(res: Result) -> str:
    out: List[str] = []
    out.append("")
    out.append(_c(f"输入  {res.input_url}", "dim"))
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
        out.append(_c("  ✓ 终点", "green") + f"  {res.final_url}")
    else:
        out.append(_c("  ✗ 未拿到终点", "red") + f"  {res.note or '未知原因'}")
        if res.final_url and res.final_url != res.input_url:
            out.append(_c(f"    最后一个地址: {res.final_url}", "dim"))
    out.append(_c(f"  方式 {res.method or '—'} · 耗时 {res.elapsed:.2f}s", "dim"))
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="linkresolver",
        description="把短链 / 广告门解析到真正的终点地址（HTTP 快路径 + Chromium 兜底）",
    )
    ap.add_argument("urls", nargs="+", help="一个或多个短链")
    ap.add_argument("--browser", action="store_true", help="强制走浏览器模式")
    ap.add_argument("--no-browser", action="store_true", help="禁止浏览器兜底，只用 HTTP")
    ap.add_argument("--headful", action="store_true", help="显示浏览器窗口（调试用）")
    ap.add_argument("--timeout", type=float, default=25.0, help="单站超时秒数（默认 25）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
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
