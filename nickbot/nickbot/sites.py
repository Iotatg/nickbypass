"""站点注册表：认得这个域名，就知道该走哪条路。

"支持"在这里有两层意思，别混：

* **认得出** —— 域名在表里，机器人能报出站点名，也不会把广告页当成终点。
  这一层有几千个域名，来自公开短链名单（见 ``registry.py`` 与
  ``tools/build_sites.py``）。
* **有专属策略** —— ``gt`` / ``gate`` / ``short`` 三种，决定是直接打站点 API、
  还是必须上浏览器。这一层是手工维护的，百来个。

表里没有的域名照样能解析 —— 通用引擎不认识它，但会用同一套规则试。
所以 ``/sites`` 的数字是"认得多少"，不是"只能解析多少"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlsplit

from . import registry

KIND_GATE = "gate"      # 倒计时 / 按钮广告门，浏览器必需
KIND_GT = "gt"          # 同模板站点，POST /links/go 直接拿终点
KIND_SHORT = "short"    # 纯 301/302，不用浏览器
KIND_LISTED = "listed"  # 只认得：是短链站，策略交给通用引擎


@dataclass(frozen=True)
class Site:
    slug: str
    name: str
    kind: str = KIND_GATE
    note: str = ""

    @property
    def needs_browser(self) -> Optional[bool]:
        """None = 不确定，让引擎自己判断（和未知域名同等待遇）。"""
        if self.kind == KIND_SHORT:
            return False
        if self.kind == KIND_LISTED:
            return None
        return True


# 手工维护的那一层
CURATED: tuple[Site, ...] = tuple(
    Site(slug=d, name=n, kind=k) for d, n, k in registry.CURATED
)
_BY_SLUG: Dict[str, Site] = {s.slug: s for s in CURATED}

# 生成的那一层（几千个，只带域名）
LISTED: tuple[str, ...] = registry.bulk_domains()
_LISTED_SET = frozenset(LISTED)

# 品牌名 -> 站点。用来兜住换后缀的镜像站：gplinks.co / gplinks.xyz 都算 GPLinks
_BRANDS: Dict[str, Site] = {}
for _s in CURATED:
    _brand = _s.slug.split(".")[0]
    if len(_brand) >= 5 and _brand not in _BRANDS:
        _BRANDS[_brand] = _s

SCRAPER_ONLY: tuple[str, ...] = registry.SCRAPER_ONLY


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def _walk(host: str):
    """a.b.example.co.in -> a.b.example.co.in, b.example.co.in, example.co.in ..."""
    parts = host.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def lookup(url: str) -> Optional[Site]:
    h = host_of(url)
    if not h:
        return None
    # 1) 精确 / 上级域名
    for cand in _walk(h):
        s = _BY_SLUG.get(cand)
        if s is not None:
            return s
    # 2) 换后缀的镜像：只看第一段，且品牌名够长，避免误伤
    labels = h.split(".")
    if labels:
        s = _BRANDS.get(labels[0])
        if s is not None:
            return s
    # 3) 生成名单
    for cand in _walk(h):
        if cand in _LISTED_SET:
            return Site(slug=cand, name=cand, kind=KIND_LISTED)
    return None


def is_scraper_only(url: str) -> Optional[str]:
    h = host_of(url)
    for d in SCRAPER_ONLY:
        if d in h:
            return d
    return None


def _short_code(url: str) -> bool:
    """``/abc123`` 这种一段式短码。深路径（/blog/post-1）说明是内容页。"""
    try:
        path = urlsplit(url).path.strip("/")
    except Exception:
        return False
    if not path or "/" in path:
        return False
    return len(path) <= 24


def is_gate(url: str) -> bool:
    """还停在短链 / 广告站上吗？

    手写的 gate / gt 一律算；其余域名只在 URL 长得像短码（``/abc123``）时才算。
    这样 ``shrtfly.com/abc`` 不会被当成终点，而 ``某站.com/blog/post`` 不会
    被误杀 —— 用户可能就是把某个内容页当链接发进来的。
    """
    s = lookup(url)
    if s is None:
        return False
    if s.kind in (KIND_GATE, KIND_GT):
        return True
    return _short_code(url)


def needs_browser(url: str) -> bool:
    """要不要给浏览器兜底留机会。未知域名一律 True（宁可慢，也别漏）。"""
    s = lookup(url)
    if s is None:
        return True
    val = s.needs_browser
    return True if val is None else val


def display_name(url: str) -> str:
    s = lookup(url)
    if s:
        return s.name
    h = host_of(url)
    return h or "unknown"


def stats() -> dict:
    gates = sum(1 for s in CURATED if s.kind in (KIND_GATE, KIND_GT))
    gt = sum(1 for s in CURATED if s.kind == KIND_GT)
    short = sum(1 for s in CURATED if s.kind == KIND_SHORT)
    return {
        "total": len(CURATED) + len(LISTED),
        "curated": len(CURATED),
        "gates": gates,
        "gt": gt,
        "short": short,
        "listed": len(LISTED),
    }


def supported_text(limit: int = 48) -> str:
    st = stats()
    gate_names = sorted({s.name for s in CURATED if s.kind in (KIND_GATE, KIND_GT)})
    short_names = sorted({s.name for s in CURATED if s.kind == KIND_SHORT})
    sample = LISTED[:: max(1, len(LISTED) // 24)][:24] if LISTED else []

    def block(names: list[str], cap: int) -> str:
        shown = names[:cap]
        rest = len(names) - len(shown)
        tail = f" …+{rest} more" if rest > 0 else ""
        return ", ".join(shown) + tail

    lines = [
        f"<b>{st['total']:,} sites on the list</b>",
        "",
        f"<b>Ad gates</b> · {st['gates']} domains, {len(gate_names)} names — "
        "countdown, button, or a 「wait 15s」 in the way",
        block(gate_names, limit),
        "",
        f"<b>Plain shorteners</b> · {len(short_names)} — one redirect and done, "
        "no browser needed",
        block(short_names, 40),
        "",
        f"<b>Recognised only</b> · {st['listed']:,} more shortener domains. "
        "No hand-written parser for each: same engine, same rules.",
        "<i>" + ", ".join(sample) + " …</i>",
        "",
        "Check one by name: <code>/sites gplinks</code>",
        "Not on the list? Paste it anyway — unknown domains go through the "
        "generic engine, so missing here doesn't mean unsupported.",
    ]
    text = "\n".join(lines)
    return text[:4000]


def search_text(q: str, limit: int = 12) -> str:
    """``/sites gplinks`` —— 按名字或域名找。"""
    q = (q or "").strip().lower()
    if not q:
        return supported_text()

    hits: list[str] = []
    for s in CURATED:
        if q in s.slug or q in s.name.lower():
            hits.append(f"<b>{s.name}</b> · <code>{s.slug}</code> · {_kind_label(s.kind)}")

    # 手工层已经答上了就别拿生成层灌水：补三条封顶
    extra = 3 if hits else 8
    for d in LISTED:
        if extra <= 0 or len(hits) >= limit:
            break
        if q in d:
            hits.append(f"<code>{d}</code> · recognised shortener")
            extra -= 1

    if not hits:
        return (
            f"Nothing matches <b>{q}</b> in the list.\n"
            "That doesn't mean it fails — paste the link and the generic engine "
            "will take a run at it."
        )
    return "<b>Matches</b>\n" + "\n".join(hits[:limit])


def _kind_label(kind: str) -> str:
    return {
        KIND_GT: "site API (fast)",
        KIND_GATE: "ad gate (browser)",
        KIND_SHORT: "plain redirect",
        KIND_LISTED: "recognised",
    }.get(kind, kind)
