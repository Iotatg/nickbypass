"""入口：

    python -m nickbot                 # 启动机器人
    python -m nickbot --check         # 只检查配置
    python -m nickbot --self-test URL # 不连 Telegram，直接跑解析管线并打印消息
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import Config


def _self_test(cfg: Config, url: str, browser: bool) -> int:
    from . import format as F
    from . import sites
    from .handlers import Runtime

    rt = Runtime(cfg)
    data = asyncio.run(rt.engine.resolve(url, allow_browser=browser))
    print("=" * 60)
    print(F.result_card(data))
    print("=" * 60)
    print(F.trace_card(data))
    print("=" * 60)
    print(f"site={sites.display_name(url)}  browser_required={sites.needs_browser(url)}")
    return 0 if data.get("ok") else 1


def _env_report(cfg: Config) -> None:
    """这台机器能干什么 — 部署时第一个要看的输出。"""
    import platform

    try:
        import playwright  # noqa: F401
        has_pw = True
    except Exception:
        has_pw = False

    print("Environment")
    print(f"  python     : {sys.version.split()[0]} · {platform.machine()} · {platform.system()}")
    print(f"  browser    : {'playwright installed' if has_pw else 'not installed — HTTP path only'}")
    print(f"  allow_browser={cfg.allow_browser} · proxy_farm={cfg.proxy_farm} "
          f"({cfg.proxy_country}) · proxy={'set' if cfg.proxy else 'none'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nickbot")
    ap.add_argument("--check", action="store_true", help="检查 .env 配置")
    ap.add_argument("--self-test", metavar="URL", help="不连 Telegram，直接解析一条链接")
    ap.add_argument("--no-browser", action="store_true", help="self-test 时禁用浏览器")
    args = ap.parse_args(argv)

    cfg = Config.load()

    if args.check:
        print("Config check")
        print(f"  token      : {'set' if cfg.token else 'missing'}"
              f"{' (' + cfg.token[:6] + '…' + cfg.token[-4:] + ')' if cfg.token else ''}")
        print(f"  owner      : {sorted(cfg.owner_ids) or 'missing'}")
        print(f"  logger chat: {cfg.logger_chat or 'not set'}")
        print(f"  db         : {cfg.db_path}")
        print(f"  engine     : {cfg.linkresolver_home}")
        print(f"  rate limit : {cfg.rate_limit}/{cfg.rate_window}s · concurrency {cfg.http_concurrency}"
              f" · browser {cfg.browser_concurrency}")
        probs = cfg.problems()
        print("  problems   : " + ("none" if not probs else "; ".join(probs)))
        print()
        _env_report(cfg)
        return 0 if not probs else 1

    if args.self_test:
        return _self_test(cfg, args.self_test, not args.no_browser)

    probs = cfg.problems()
    if probs:
        print("Startup failed:")
        for p in probs:
            print("  -", p)
        print("\nCopy .env.example to .env and fill BOT_TOKEN / OWNER_ID first.")
        return 2

    logging.basicConfig(
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from .bot import run

    print("Nick Bypass Bot starting… Ctrl+C to stop")
    run(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
