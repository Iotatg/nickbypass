"""Extraction rules: find the next hop inside HTML / query parameters.

Three layers:
1. Site rules (SITE_RULES): host match -> bespoke decoder
2. Generic extractors (GENERIC): meta refresh / JS location / inline JSON /
   base64 parameters / auto-submitted forms / continue buttons
3. Domain knowledge (NEEDS_BROWSER / HINTS): which sites need a browser, and
   what to tell the user about them

Every rule only reads a page and looks for the next hop; it never sends a
request. Requests belong to the engine.
"""

from __future__ import annotations

import base64
import binascii
import html as htmlmod
import re
import urllib.parse as up
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


# ------------------------------------------------------------ domain knowledge

# These sites rely on JS timers, dynamic tokens and popups; plain HTTP never
# reaches the destination, so they need a browser.
NEEDS_BROWSER = {
    "linkvertise.com", "link-to.net", "linkvertise.net",
    "ouo.io", "ouo.press",
    "gplinks.co", "gplinks.in", "gplink.in",
    "shrinkme.io", "shrinke.me", "shrinkme.ink",
    "adfoc.us", "boost.ink", "exe.io", "exeo.app",
    "za.gl", "zagl.", "clk.sh", "droplink.co", "mdisk.pro",
    "aylink.co", "tnlink.in", "tnshort.net", "mdiskshortner.link",
    "shrtfly.com", "shrtfly.net", "try2link.com", "urlshortx.com",
    "safelinku.com", "sfl.gl", "cuty.io", "tii.ai", "indianshortner.in",
    "earn4link.in", "openurl.co", "websiteplanet.com",
}

# Plain redirect shorteners (no ad gate): one HTTP hop is enough.
PLAIN_SHORTENERS = {
    "bit.ly", "bitly.com", "tinyurl.com", "t.co", "goo.gl", "is.gd", "v.gd",
    "cutt.ly", "rebrand.ly", "shorturl.at", "s.id", "srt.am", "t.ly",
    "bl.ink", "short.io", "rb.gy", "clck.ru", "vk.cc", "u.to", "x.co",
    "tiny.cc", "lnkd.in", "trib.al", "dlvr.it", "ift.tt", "buff.ly",
    "ow.ly", "short.link", "1url.com", "cutt.us", "shorte.st", "adf.ly",
}

HINTS = {
    "linkvertise.com": "Linkvertise: 3s countdown + popups + a dynamic token, needs browser mode",
    "ouo.io": "ouo.io: form + reCAPTCHA, plain HTTP only gets the intermediate page",
    "gplinks.co": "GPLinks: POST /links/go + dynamic ad_form_data, needs browser mode",
    "adfoc.us": "adfoc.us: JS timer, needs browser mode",
    "adf.ly": "adf.ly: the ysmm variable can be decoded without a browser",
}


# ------------------------------------------------------------------- helpers

def host_of(url: str) -> str:
    try:
        return (up.urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def _norm_host(h: str) -> str:
    h = h.lower().lstrip(".")
    return h[4:] if h.startswith("www.") else h


def matches(host: str, domains) -> bool:
    host = _norm_host(host)
    return any(host == d or host.endswith("." + d) for d in domains)


def b64decode_any(s: str) -> Optional[str]:
    """Best-effort base64 decode (urlsafe / missing padding / double-encoded)."""
    if not s:
        return None
    s = htmlmod.unescape(s.strip())
    s = up.unquote(s)
    for _ in range(2):
        t = s.strip().replace("-", "+").replace("_", "/")
        t += "=" * (-len(t) % 4)
        try:
            raw = base64.b64decode(t, validate=False)
        except (binascii.Error, ValueError):
            return None
        try:
            out = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if "http" in out or out.startswith("/"):
            return out
        s = out
    return None


def _abs(base: str, cand: str) -> Optional[str]:
    if not cand:
        return None
    cand = htmlmod.unescape(cand).strip().strip("'\"")
    cand = cand.replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
    if cand.startswith("//"):
        return "https:" + cand
    if cand.startswith("http://") or cand.startswith("https://"):
        return cand
    if cand.startswith("/"):
        return up.urljoin(base, cand)
    return None


def _looks_like_target(url: str, base: str) -> bool:
    """Drop things that are obviously not a destination (same-site home, assets, JS files)."""
    if not url:
        return False
    if re.search(r"\.(js|css|png|jpe?g|gif|svg|woff2?|ico|mp4)(\?|$)", url, re.I):
        return False
    if url.rstrip("/") in (base.rstrip("/"), "https://" + host_of(base), "http://" + host_of(base)):
        return False
    return True


# ----------------------------------------------------------------- site rules

@dataclass
class Ctx:
    url: str
    html: str
    soup: object = None
    params: Dict[str, List[str]] = None  # type: ignore

    @property
    def text(self) -> str:
        if self.soup is not None:
            return self.soup.get_text(" ", strip=True)
        return re.sub(r"<[^>]+>", " ", self.html)

    def param(self, *names: str) -> Optional[str]:
        for n in names:
            if self.params and self.params.get(n):
                return self.params[n][0]
        return None


def rule_adfly(ctx: Ctx) -> Optional[Tuple[str, str]]:
    """adf.ly / j.gs / q.gs: ysmm variable + odd/even split + base64."""
    m = re.search(r"ysmm\s*=\s*['\"]([^'\"]+)['\"]", ctx.html)
    if not m:
        return None
    ysmm = m.group(1)
    a, b = "", ""
    for i, ch in enumerate(ysmm):
        if i % 2 == 0:
            a += ch
        else:
            b = ch + b
    decoded = b64decode_any(a + b) or ""
    idx = decoded.find("http")
    if idx < 0:
        return None
    return decoded[idx:], "adf.ly ysmm decoded"


def rule_b64_param(ctx: Ctx) -> Optional[Tuple[str, str]]:
    """The old ?url= / ?r= / ?s= / ?id= base64 trick that most safelinks use."""
    for key in ("url", "u", "r", "s", "id", "go", "to", "link", "target", "dest", "redirect", "q"):
        raw = ctx.param(key)
        if not raw:
            continue
        dec = b64decode_any(raw)
        if dec:
            nxt = _abs(ctx.url, dec)
            if nxt and _looks_like_target(nxt, ctx.url):
                return nxt, f"?{key}= base64 decoded"
    return None


def rule_vk_away(ctx: Ctx) -> Optional[Tuple[str, str]]:
    if "away.php" in ctx.url:
        to = ctx.param("to")
        if to:
            nxt = _abs(ctx.url, up.unquote(to))
            if nxt:
                return nxt, "vk away.php ?to="
    return None


def rule_google_redirect(ctx: Ctx) -> Optional[Tuple[str, str]]:
    if re.search(r"google\.[a-z.]+/url\?", ctx.url):
        for key in ("q", "url"):
            v = ctx.param(key)
            if v and v.startswith("http"):
                return v, "google /url redirect"
    return None


SITE_RULES = [
    (("adf.ly", "j.gs", "q.gs", "adyou.me"), rule_adfly),
    (("vk.com", "vk.ru"), rule_vk_away),
    (("google.com", "google.co.in", "google.co.uk", "googleusercontent.com"), rule_google_redirect),
]


# ---------------------------------------------------------- generic extractors

_META_RE = re.compile(
    r"<meta[^>]+http-equiv\s*=\s*['\"]?refresh['\"]?[^>]*content\s*=\s*['\"]([^'\"]+)['\"]", re.I
)
_META_RE2 = re.compile(
    r"<meta[^>]+content\s*=\s*['\"]([^'\"]+)['\"][^>]*http-equiv\s*=\s*['\"]?refresh", re.I
)

_JS_PATTERNS = [
    r"(?:window\.|document\.|top\.|self\.|parent\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
    r"location\.(?:replace|assign)\(\s*['\"]([^'\"]+)['\"]",
    r"(?:window\.|top\.)?location\.href\s*=\s*['\"]([^'\"]+)['\"]",
    r"document\.location\s*=\s*['\"]([^'\"]+)['\"]",
]

_JSON_URL_RE = re.compile(
    r"['\"](?:url|link|target|destination|redirect|dest|href)['\"]\s*:\s*['\"](https?:\\?/\\?/[^'\"]{6,})['\"]",
    re.I,
)

_CONTINUE_TEXT = re.compile(
    r"(continue|get\s*link|proceed|skip\s*ad|click\s*here|download|verify|"
    r"i'?m\s*not\s*a\s*robot|go\s*to\s*link|get\s*it|continue|skip)",
    re.I,
)


def extract_meta(html: str) -> Optional[str]:
    for rx in (_META_RE, _META_RE2):
        m = rx.search(html)
        if m:
            content = m.group(1)
            mm = re.search(r"url\s*=\s*['\"]?([^'\";]+)", content, re.I)
            if mm:
                return mm.group(1).strip()
    return None


def extract_js(html: str) -> Optional[Tuple[str, str]]:
    for i, pat in enumerate(_JS_PATTERNS):
        for m in re.finditer(pat, html):
            cand = m.group(1)
            if cand and ("http" in cand or cand.startswith("/")):
                return cand, f"JS location redirect (pattern {i + 1})"
    return None


def extract_json(html: str) -> Optional[Tuple[str, str]]:
    m = _JSON_URL_RE.search(html)
    if m:
        return m.group(1), "url field in inline JSON"
    return None


def extract_anchor(html: str, base: str) -> Optional[Tuple[str, str]]:
    """A continue / go button that already carries the destination (v.gd style pages)."""
    if BeautifulSoup is None:
        return None
    soup = BeautifulSoup(html, "html.parser")
    base_host = _norm_host(host_of(base))
    for a in soup.find_all(["a", "button"]):
        href = a.get("href") or a.get("data-href") or a.get("data-url") or ""
        if not href.startswith("http"):
            continue
        label = a.get_text(" ", strip=True)[:60]
        cls = " ".join(a.get("class") or []) + " " + (a.get("id") or "")
        if _norm_host(host_of(href)) == base_host:
            continue
        hit = (
            bool(_CONTINUE_TEXT.search(label))
            or label.startswith("http")           # destination URL used as the button label
            or bool(re.search(r"(biglink|big-link|btn|button|continue|direct|goto)", cls, re.I))
        )
        if hit and _looks_like_target(href, base):
            return href, f"Page button \"{label or cls.strip() or 'continue'}\" points at the destination"
    return None


def extract_form(html: str, base: str) -> Optional[Tuple[str, str, Dict[str, str]]]:
    """Auto-submitted form: returns (action, detail, fields)."""
    if BeautifulSoup is None:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        action = form.get("action") or ""
        method = (form.get("method") or "get").lower()
        if method != "get":
            continue  # POST forms go to browser mode; guessing tokens there is a waste of time
        fields: Dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            fields[name] = inp.get("value") or ""
        joined = " ".join(fields.values())
        if any(v.startswith("http") for v in fields.values()) or re.search(r"https?://", joined):
            return up.urljoin(base, action or base), "GET form auto-submitted", fields
    return None


# ---------------------------------------------------------------- entry point

def extract_next(url: str, html: str) -> Optional[Tuple[str, str, Optional[Dict[str, str]]]]:
    """Returns (next hop URL, explanation, form fields | None)."""
    host = host_of(url)
    params = up.parse_qs(up.urlsplit(url).query)
    soup = BeautifulSoup(html, "html.parser") if BeautifulSoup else None
    ctx = Ctx(url=url, html=html, soup=soup, params=params)

    for domains, fn in SITE_RULES:
        if matches(host, domains):
            try:
                got = fn(ctx)
            except Exception:
                got = None
            if got:
                return _abs(url, got[0]) or got[0], got[1], None

    for fn in (rule_b64_param,):
        got = fn(ctx)
        if got:
            return got[0], got[1], None

    cand = extract_meta(html)
    if cand:
        nxt = _abs(url, cand)
        if nxt and _looks_like_target(nxt, url):
            return nxt, "meta refresh redirect", None

    got = extract_js(html)
    if got:
        nxt = _abs(url, got[0])
        if nxt and _looks_like_target(nxt, url):
            return nxt, got[1], None

    got = extract_json(html)
    if got:
        nxt = _abs(url, got[0])
        if nxt and _looks_like_target(nxt, url):
            return nxt, got[1], None

    got = extract_anchor(html, url)
    if got:
        return got[0], got[1], None

    form = extract_form(html, url)
    if form:
        action, detail, fields = form
        qs = up.urlencode(fields)
        joiner = "&" if "?" in action else "?"
        return f"{action}{joiner}{qs}", detail, fields

    return None
