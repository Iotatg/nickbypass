"""Entry point:

    python -m nickbot                 # start the bot
    python -m nickbot --check         # config check only
    python -m nickbot --self-test URL # run the pipeline, no Telegram connection
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
    """What this machine can do - the first thing to look at when deploying."""
    import platform

    try:
        import playwright  # noqa: F401
        has_pw = True
    except Exception:
        has_pw = False

    try:
        import h2  # noqa: F401
        has_h2 = True
    except Exception:
        has_h2 = False

    try:
        import pyrogram
        has_kuri = True
        kuri_ver = pyrogram.__version__
    except Exception:
        has_kuri = False
        kuri_ver = ""

    backend = "kurigram" if cfg.use_kurigram() else "python-telegram-bot"
    print("Environment")
    print(f"  python     : {sys.version.split()[0]} · {platform.machine()} · {platform.system()}")
    print(f"  backend    : {backend}"
          f"{' ' + kuri_ver if has_kuri and backend == 'kurigram' else ''}"
          f"{'' if has_kuri or backend != 'kurigram' else ' (pip install kurigram)'}")
    print(f"  http/2     : {'on (h2 installed)' if has_h2 else 'off (pip install h2 to enable)'}")
    print(f"  browser    : {'playwright installed' if has_pw else 'not installed - HTTP path only'}")
    print(f"  allow_browser={cfg.allow_browser} · proxy_farm={cfg.proxy_farm} "
          f"({cfg.proxy_country}) · proxy={'set' if cfg.proxy else 'none'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nickbot")
    ap.add_argument("--check", action="store_true", help="check the .env configuration")
    ap.add_argument("--self-test", metavar="URL", help="resolve one link without connecting to Telegram")
    ap.add_argument("--no-browser", action="store_true", help="disable the browser during --self-test")
    args = ap.parse_args(argv)

    cfg = Config.load()

    if args.check:
        print("Config check")
        print(f"  token      : {'set' if cfg.token else 'missing'}"
              f"{' (' + cfg.token[:6] + '…' + cfg.token[-4:] + ')' if cfg.token else ''}")
        print(f"  api id     : {cfg.api_id or 'missing (Kurigram only)'}")
        print(f"  api hash   : {'set' if cfg.api_hash else 'missing (Kurigram only)'}")
        print(f"  backend    : {cfg.backend} -> {'kurigram' if cfg.use_kurigram() else 'ptb'}")
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
        if cfg.backend == "kurigram":
            print("Kurigram also needs API_ID and API_HASH from https://my.telegram.org.")
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
