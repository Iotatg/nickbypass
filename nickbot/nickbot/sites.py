"""站点注册表。

说明：这里的"支持"= 机器人认得这个域名，会用它对应的策略去解析
（HTTP 快路径 / 浏览器兜底）。**不是**每个站都手写了解析器——
现代广告门的终点是 JS 运行时生成的，写死解析器两周就烂，通用引擎反而更耐用。

slug 用"子串匹配"，这样 .co / .in / .com / .xyz 换域名也能命中。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Site:
    slug: str
    name: str
    needs_browser: bool = True
    kind: str = "gate"          # gate | shortener
    note: str = ""


# 你那份更新日志里出现的站，按"是否必须上浏览器"分了两类
SITES: tuple[Site, ...] = (
    # —— 典型 JS 广告门（倒计时 / 动态 token / 弹窗）
    Site("earn4link", "Earn4Link"),
    Site("earnlink", "EarnLinks"),
    Site("gplinks", "GPLinks"),
    Site("gplink", "GPLinks"),
    Site("arolinks", "AroLinks"),
    Site("vplinks", "VPLinks"),
    Site("vplink", "VPLink"),
    Site("vipshort", "VipShort"),
    Site("cpmshort", "CPMShort"),
    Site("nowshort", "NowShort"),
    Site("liteshort", "LiteShort"),
    Site("liteurl", "LiteURL"),
    Site("inshorturl", "InShortURL"),
    Site("indianshortener", "IndianShortener"),
    Site("indianshortner", "IndianShortener"),
    Site("babylinks", "BabyLinks"),
    Site("indiaearnx", "IndiaEarnX"),
    Site("submais", "Submais"),
    Site("unlocktoearn", "UnlockToEarn"),
    Site("sub4unlock", "Sub4Unlock"),
    Site("easysky", "EasySky"),
    Site("linkpays", "LinkPays"),
    Site("makelinks", "MakeLinks"),
    Site("instantlinks", "InstantLinks"),
    Site("bindaaslinks", "BindaasLinks"),
    Site("linksflys", "LinksFlys"),
    Site("linksfire", "LinksFire"),
    Site("srnky", "Srnky"),
    Site("shortwala", "ShortWala"),
    Site("lksfy", "LKSFY"),
    Site("iflylink", "IFlyLink"),
    Site("alpharede", "AlphaRede"),
    Site("short4cash", "Short4Cash"),
    Site("surajitlinks", "SurajitLinks"),
    Site("rempo", "Rempo"),
    Site("remso", "Remso"),
    Site("kogap", "Kogap"),
    Site("exe.io", "Exe.io"),
    Site("cuty.io", "Cuty.io"),
    Site("oii.la", "Oii.la"),
    Site("tii.la", "Tii.la"),
    Site("tpi.li", "TPI"),
    Site("mdisk", "MDisk"),
    Site("aylink", "Aylink"),
    Site("tnlink", "TNLink"),
    Site("shrinkme", "ShrinkMe"),
    Site("shrinke", "ShrinkMe"),
    Site("linkvertise", "Linkvertise"),
    Site("adfoc", "AdFoc.us"),
    Site("ouo.io", "Ouo.io"),
    Site("ouo.press", "Ouo.press"),
    Site("boost.ink", "Boost.ink"),
    Site("clk.sh", "Clk.sh"),
    Site("za.gl", "Za.gl"),
    Site("droplink", "DropLink"),

    # —— 纯跳转，不用浏览器
    Site("bit.ly", "Bit.ly", needs_browser=False, kind="shortener"),
    Site("tinyurl", "TinyURL", needs_browser=False, kind="shortener"),
    Site("is.gd", "is.gd", needs_browser=False, kind="shortener"),
    Site("v.gd", "v.gd", needs_browser=False, kind="shortener"),
    Site("cutt.ly", "Cutt.ly", needs_browser=False, kind="shortener"),
    Site("rebrand.ly", "Rebrand.ly", needs_browser=False, kind="shortener"),
    Site("shorturl.at", "ShortURL", needs_browser=False, kind="shortener"),
    Site("t.co", "t.co", needs_browser=False, kind="shortener"),
    Site("rb.gy", "RB.gy", needs_browser=False, kind="shortener"),
    Site("tiny.cc", "TinyCC", needs_browser=False, kind="shortener"),
)

# 这些是"文件托管 / 内容站"，要拿到直链得写站点爬虫，属于抓盗版资源那一类，
# 本机器人只做跳转展开，不做内容抓取。
SCRAPER_ONLY: tuple[str, ...] = (
    "hubcloud", "hubdrive", "gdflix", "gdtot", "zdrive", "katdrive",
    "toonworld4all", "animekaizou", "filepress", "drivebot",
)

_BY_SLUG = {s.slug: s for s in SITES}


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def lookup(url: str) -> Optional[Site]:
    h = host_of(url)
    if not h:
        return None
    for site in SITES:
        if site.slug in h:
            return site
    return None


def is_scraper_only(url: str) -> Optional[str]:
    h = host_of(url)
    for d in SCRAPER_ONLY:
        if d in h:
            return d
    return None


def is_gate(url: str) -> bool:
    """已知的广告门站点（不在名单里的域名返回 False）。"""
    s = lookup(url)
    return bool(s and s.needs_browser)


def needs_browser(url: str) -> bool:
    s = lookup(url)
    return True if s is None else s.needs_browser


def display_name(url: str) -> str:
    s = lookup(url)
    if s:
        return s.name
    h = host_of(url)
    return h or "unknown"


def supported_text(limit: int = 60) -> str:
    gates = [s.name for s in SITES if s.needs_browser]
    plain = [s.name for s in SITES if not s.needs_browser]
    lines = [
        f"<b>Ad gates</b> — {len(gates)} sites",
        ", ".join(sorted(set(gates))[:limit]),
        "",
        f"<b>Plain shorteners</b> — {len(plain)} sites",
        ", ".join(sorted(set(plain))[:limit]),
        "",
        "<i>Unknown domains still go through the generic engine — "
        "not being listed does not mean unsupported.</i>",
    ]
    return "\n".join(lines)
