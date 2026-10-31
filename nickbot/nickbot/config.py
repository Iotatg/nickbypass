"""配置：全部从环境变量 / .env 读取，代码里不出现任何 token。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


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
    owner_ids: set[int] = field(default_factory=set)
    logger_chat: int = 0

    rate_limit: int = 6          # 每个用户每窗口允许的解析次数
    rate_window: int = 60        # 窗口秒数
    max_urls: int = 5            # 单条消息最多处理几个链接

    http_timeout: float = 12.0
    browser_timeout: float = 20.0
    max_hops: int = 12
    allow_browser: bool = True
    http_concurrency: int = 4
    browser_concurrency: int = 1

    db_path: Path = BASE_DIR / "data" / "nickbot.db"
    proxy: str = ""
    auto_proxy: bool = False
    proxy_farm: bool = False      # 用按国家校验的代理农场（印度池）
    proxy_country: str = "IN"
    proxy_budget: float = 9.0   # 换 IP 重试的总时间预算（秒）
    linkresolver_home: Path = BASE_DIR.parent / "linkresolver"

    @classmethod
    def load(cls) -> "Config":
        c = cls()
        c.token = (os.getenv("BOT_TOKEN") or "").strip()
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

    def problems(self) -> list[str]:
        out = []
        if not self.token:
            out.append("BOT_TOKEN 未设置")
        elif ":" not in self.token:
            out.append("BOT_TOKEN 格式不对（应为 123456:ABC-DEF…）")
        if not self.owner_ids:
            out.append("OWNER_ID 未设置（管理员命令将不可用）")
        if not (self.linkresolver_home / "linkresolver").is_dir():
            out.append(f"找不到解析引擎：{self.linkresolver_home}/linkresolver")
        return out

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids


ENV_FILE = BASE_DIR / ".env"


def set_env_value(key: str, value: str) -> None:
    """把 KEY=value 写回 .env（保留其它行），并同步到当前进程。"""
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
            # 重复的同名行直接丢掉
            continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")

    try:
        path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
        path.chmod(0o600)
    except Exception:
        pass
