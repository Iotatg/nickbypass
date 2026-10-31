"""Site registry: recognise the domain, know which route to take.

"Supported" means two different things here, and mixing them up is the easy mistake:

* **recognised** - the domain is in the table, so the bot can name the site and
  will not mistake its ad page for a destination. Thousands of domains, taken
  from public shortener lists (see ``registry.py`` and
  ``tools/build_sites.py``）。
* **has a dedicated strategy** - one of ``gt`` / ``gate`` / ``short``, which
decides whether we call the site API or need a browser. Hand-maintained, ~180.

A domain that is not in the table still gets resolved: the generic engine does
not know it by name, but it tries the same rules. So the number in ``/sites`` is
"how many I recognise", not "how many I can resolve".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlsplit

from . import registry

KIND_GATE = "gate"      # countdown / button ad gate, browser required
KIND_GT = "gt"          # shared template, POST /links/go hands over the destination
KIND_SHORT = "short"    # plain 301/302, no browser needed
KIND_LISTED = "listed"  # recognised only: a shortener, strategy left to the engine


@dataclass(frozen=True)
class Site:
    slug: str
    name: str
    kind: str = KIND_GATE
    note: str = ""

    @property
    def needs_browser(self) -> Optional[bool]:
        """None = not sure, let the engine decide (same treatment as an unknown domain)."""
        if self.kind == KIND_SHORT:
            return False
        if self.kind == KIND_LISTED:
            return None
        return True


# the hand-maintained layer
CURATED: tuple[Site, ...] = tuple(
    Site(slug=d, name=n, kind=k) for d, n, k in registry.CURATED
)
_BY_SLUG: Dict[str, Site] = {s.slug: s for s in CURATED}

# the generated layer (thousands of domains, domain only)
LISTED: tuple[str, ...] = registry.bulk_domains()
_LISTED_SET = frozenset(LISTED)

# brand name -> site. Catches mirror domains that swap the suffix:
# gplinks.co and gplinks.xyz are both GPLinks
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
    # 1) exact / parent domain
    for cand in _walk(h):
        s = _BY_SLUG.get(cand)
        if s is not None:
            return s
    # 2) suffix-swapped mirrors: first label only, and only for long enough brands,
#    so a random subdomain cannot collide
    labels = h.split(".")
    if labels:
        s = _BRANDS.get(labels[0])
        if s is not None:
            return s
    # 3) generated list
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
    """A one-segment short code like ``/abc123``. A deep path (/blog/post-1) means a content page."""
    try:
        path = urlsplit(url).path.strip("/")
    except Exception:
        return False
    if not path or "/" in path:
        return False
    return len(path) <= 24


def is_gate(url: str) -> bool:
    """Are we still on the shortener / ad site?

    Hand-written gate / gt entries always count. Other domains only count when
    the URL looks like a short code (``/abc123``), so ``shrtfly.com/abc`` is not
    treated as a destination while ``some-site.com/blog/post`` is not killed by
    mistake - the user may well have pasted a content page on purpose.
    """
    s = lookup(url)
    if s is None:
        return False
    if s.kind in (KIND_GATE, KIND_GT):
        return True
    return _short_code(url)


def needs_browser(url: str) -> bool:
    """Should the browser fallback get a shot? Unknown domains: always yes (rather slow than wrong)."""
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
    """``/sites gplinks`` - look a site up by name or domain."""
    q = (q or "").strip().lower()
    if not q:
        return supported_text()

    hits: list[str] = []
    for s in CURATED:
        if q in s.slug or q in s.name.lower():
            hits.append(f"<b>{s.name}</b> · <code>{s.slug}</code> · {_kind_label(s.kind)}")

    # If the curated layer answered, do not flood it with generated entries: cap at three.
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
