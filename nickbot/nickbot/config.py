"""Configuration: everything comes from environment variables / .env.

No token is ever hard-coded here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    # Read the project-root .env first (works no matter where the bot is started
    # from), then fall back to the current directory.
    if not load_dotenv(BASE_DIR / ".env"):
        load_dotenv()
except Exception:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "") or ""
    out: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


@dataclass
class Config:
    token: str = ""
    api_id: int = 0              # Kurigram / MTProto credentials (my.telegram.org)
    api_hash: str = ""
    backend: str = "auto"        # auto | kurigram | ptb
    owner_ids: set[int] = field(default_factory=set)
    logger_chat: int = 0

    rate_limit: int = 6          # resolutions allowed per user per window
    rate_window: int = 60        # window length in seconds
    max_urls: int = 5            # links handled per message

    http_timeout: float = 12.0
    browser_timeout: float = 20.0
    max_hops: int = 12
    allow_browser: bool = True
    http_concurrency: int = 4
    browser_concurrency: int = 1

    db_path: Path = BASE_DIR / "data" / "nickbot.db"
    proxy: str = ""
    auto_proxy: bool = False
    proxy_farm: bool = False     # country-verified proxy farm
    proxy_country: str = "IN"
    proxy_budget: float = 9.0    # total time budget for an IP-rotation retry
    linkresolver_home: Path = BASE_DIR.parent / "linkresolver"

    @classmethod
    def load(cls) -> "Config":
        c = cls()
        c.token = (os.getenv("BOT_TOKEN") or "").strip()
        c.api_id = _int("API_ID", 0)
        c.api_hash = (os.getenv("API_HASH") or "").strip()
        c.backend = (os.getenv("BACKEND") or "auto").strip().lower()
        c.owner_ids = _ids("OWNER_ID")
        c.logger_chat = _int("LOGGER_CHAT_ID", 0)
        c.rate_limit = _int("RATE_LIMIT", 6)
        c.rate_window = _int("RATE_WINDOW", 60)
        c.max_urls = _int("MAX_URLS", 5)
        c.http_timeout = float(_int("HTTP_TIMEOUT", 12))
        c.browser_timeout = float(_int("BROWSER_TIMEOUT", 20))
        c.max_hops = _int("MAX_HOPS", 12)
        c.allow_browser = _bool("ALLOW_BROWSER", True)
        c.http_concurrency = _int("HTTP_CONCURRENCY", 4)
        c.browser_concurrency = _int("BROWSER_CONCURRENCY", 1)
        c.proxy = (os.getenv("PROXY") or "").strip()
        c.auto_proxy = _bool("AUTO_PROXY", False)
        c.proxy_farm = _bool("PROXY_FARM", False)
        c.proxy_country = (os.getenv("PROXY_COUNTRY") or "IN").strip().upper()[:2] or "IN"
        c.proxy_budget = float(_int("PROXY_BUDGET", 9))
        c.db_path = Path(os.getenv("DB_PATH") or (BASE_DIR / "data" / "nickbot.db"))
        c.linkresolver_home = Path(os.getenv("LINKRESOLVER_HOME") or (BASE_DIR.parent / "linkresolver"))
        return c

    # ------------------------------------------------------------------ backend

    def kurigram_ready(self) -> bool:
        """Kurigram talks MTProto, so it needs an api_id/api_hash pair."""
        return bool(self.api_id and self.api_hash)

    def use_kurigram(self) -> bool:
        if self.backend == "ptb":
            return False
        if self.backend == "kurigram":
            return True
        # auto: Kurigram when the MTProto credentials are there, otherwise PTB
        return self.kurigram_ready()

    # ------------------------------------------------------------------- checks

    def problems(self) -> list[str]:
        out = []
        if not self.token:
            out.append("BOT_TOKEN is not set")
        elif ":" not in self.token:
            out.append("BOT_TOKEN looks wrong (expected 123456:ABC-DEF...)")
        if self.backend == "kurigram" and not self.kurigram_ready():
            out.append("BACKEND=kurigram needs API_ID and API_HASH (my.telegram.org)")
        if not self.owner_ids:
            out.append("OWNER_ID is not set - admin commands will be unavailable")
        if not (self.linkresolver_home / "linkresolver").is_dir():
            out.append(
                f"resolver engine not found at {self.linkresolver_home}/linkresolver - "
                "clone the whole repo, or set LINKRESOLVER_HOME"
            )
        return out

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids


ENV_FILE = BASE_DIR / ".env"


def set_env_value(key: str, value: str) -> None:
    """Write KEY=value back to .env (keeping the other lines) and to this process."""
    os.environ[key] = value
    path = ENV_FILE
    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []

    out: list[str] = []
    replaced = False
    for line in lines:
        s = line.strip()
        if s.startswith(f"{key}=") or s.startswith(f"{key} ="):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            # drop duplicated lines with the same key
            continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")

    try:
        path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
        path.chmod(0o600)
    except Exception:
        pass
