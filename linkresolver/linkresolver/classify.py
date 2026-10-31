"""Does this URL *look* like a waypoint, and is there really another hop?

Kept in step with ``nickbot/hops.py``: this package is installed on its own on a
phone, so it cannot import from the bot. The bot's test suite compares both
copies over the same URL table, so they cannot drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlsplit

# "Worth a second look" at or above this score. 3 means "one strong signal, or
# two weak ones that point the same way" - a lone /generate or a lone ?code=
# is not enough on its own.
THRESHOLD = 3

# What decide() can return.
CONTINUE = "continue"
FINAL = "final"

GATE_SEGMENTS: Set[str] = {
    "generate", "generated", "generator",
    "auth", "authorize", "authorization", "oauth",
    "resolve", "resolver",
    "redirect", "redir", "redirection",
    "go", "goto", "out", "away",
    "link", "links", "linkout",
    "bypass", "unlock", "access",
    "short", "shorten", "shortener",
    "verify", "verification", "validate",
    "continue", "proceed", "next", "step",
    "captcha", "challenge", "checkpoint",
    "gateway", "interstitial", "wait",
}

# Subdomains that almost always mean "this host exists to move you along".
GATE_SUBDOMAINS: Set[str] = {
    "auth", "api", "gate", "go", "link", "links", "redirect", "out",
    "r", "s", "t", "short", "url", "verify", "secure", "gateway", "get",
}

# Serverless / static hosts where throwaway redirect endpoints are spun up.
GENERATED_SUFFIXES: Tuple[str, ...] = (
    "workers.dev", "pages.dev", "vercel.app", "netlify.app", "web.app",
    "firebaseapp.com", "glitch.me", "repl.co", "onrender.com", "herokuapp.com",
    "azurewebsites.net", "r2.dev", "deno.dev", "fly.dev",
)

# ?url=..., ?to=..., ?redirect=... - the page's whole job is to forward you.
REDIRECT_PARAMS: Set[str] = {
    "url", "u", "to", "target", "dest", "destination", "redirect",
    "redirect_uri", "goto", "go", "out", "link", "continue", "next",
}

# Opaque credentials handed out by a gate: only count when the value looks
# machine-made, so ?code=SUMMER20 on a shop page is left alone.
OPAQUE_PARAMS: Set[str] = {
    "code", "token", "key", "sig", "signature", "state", "session",
    "sessionid", "auth", "nonce", "hash", "ticket", "id",
}

GATE_TEXT: Tuple[str, ...] = (
    "please wait", "just a moment", "one moment", "redirecting",
    "you will be redirected", "you are being redirected", "generating",
    "verifying", "preparing your link", "get link", "click here to continue",
    "continue to", "checking your browser", "processing your request",
)

BLOCK_TEXT: Tuple[str, ...] = (
    "g-recaptcha", "h-captcha", "hcaptcha", "cf-turnstile", "captcha",
    "verify you are human", "verify you are a human", "are you a robot",
    "checking your browser", "just a moment", "attention required",
    "enable javascript and cookies to continue",
)

# Words that make a path segment look like a slug from a real article.
_WORDY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")
_OPAQUE_VALUE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_\-]{8,}$")
_EXT = re.compile(r"\.(?:html?|php|aspx?|jsp|do)$", re.I)
# /abc123, /N8BCN7, /9f8e7d - a code, not a slug and not a word.
_SHORT_CODE = re.compile(r"^(?=.*\d)[A-Za-z0-9]{4,16}$")


@dataclass
class Verdict:
    """How much a URL *resembles* a waypoint, with the reasons why.

    ``suspicious`` is a hint for the resolver ("spend a browser on this page"),
    not a judgement about the chain. Nothing should refuse a URL because of it.
    """

    suspicious: bool
    score: int
    reasons: List[str] = field(default_factory=list)

    @property
    def why(self) -> str:
        return ", ".join(self.reasons) or "no gate signals"


@dataclass
class Decision:
    """What to do with the address a hop just reached.

    ``action`` is ``CONTINUE`` (with ``url`` holding the next hop) or ``FINAL``
    (with ``url`` holding the accepted destination). ``reason`` says which piece
    of evidence produced it; ``hint`` keeps the pattern score for the log.
    """

    action: str
    url: str
    reason: str
    hint: Verdict

    @property
    def continues(self) -> bool:
        return self.action == CONTINUE


def normalize(url: str) -> str:
    """Same shape the resolver uses for its visited-set keys."""
    u = (url or "").strip().strip("'\"<>")
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    scheme, sep, rest = u.partition("://")
    host, slash, path = rest.partition("/")
    return f"{scheme.lower()}{sep}{host.lower()}{slash}{path}"


def host_of(url: str) -> str:
    try:
        return (urlsplit(normalize(url)).hostname or "").lower()
    except ValueError:
        return ""


def _segments(path: str) -> List[str]:
    return [s for s in (path or "").split("/") if s]


def _segment_is_gate(seg: str) -> bool:
    """``/generate`` and ``/generate.php`` yes; ``/how-to-generate`` no."""
    s = _EXT.sub("", seg.lower())
    if s in GATE_SEGMENTS:
        return True
    head = re.split(r"[-_.]", s, maxsplit=1)[0]
    return head in GATE_SEGMENTS and head != s


def _query(url: str) -> Dict[str, str]:
    try:
        return {k.lower(): v for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
    except ValueError:
        return {}


def looks_blocked(*parts: str) -> str:
    """Human-verification / anti-bot wall? Returns the reason, or ''."""
    blob = " ".join(p for p in parts if p).lower()
    if not blob:
        return ""
    for marker in BLOCK_TEXT:
        if marker in blob:
            return f"Human verification required ({marker})"
    return ""


def looks_like_waypoint(
    url: str,
    *,
    prev_url: str = "",
    title: str = "",
    text: str = "",
    known_gate: Optional[bool] = None,
) -> Verdict:
    """Score how much ``url`` resembles a gate / generated endpoint.

    Advisory only. ``prev_url`` is the hop that produced this URL, ``title`` and
    ``text`` are page evidence when we have it. ``known_gate`` lets a caller pass
    the registry's answer instead of paying for the lookup twice.
    """
    reasons: List[str] = []
    score = 0
    u = normalize(url)
    if not u:
        return Verdict(False, 0, ["empty URL"])

    try:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        path = parts.path or "/"
    except ValueError:
        return Verdict(False, 0, ["unparseable URL"])

    # 1. Known shortener / gate host. Definitive on its own.
    if known_gate is None:
        try:
            from . import sites

            known_gate = sites.is_gate(u)
        except Exception:  # pragma: no cover - sites always present in the bot
            known_gate = False
    if known_gate:
        score += 4
        reasons.append("known shortener/gate host")

    # 2. Host shape: auth./go./link. and serverless hosts.
    labels = host.split(".")
    if len(labels) > 2 and labels[0] in GATE_SUBDOMAINS:
        score += 1
        reasons.append(f"subdomain '{labels[0]}'")
    for suffix in GENERATED_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            score += 1
            reasons.append(f"generated hosting ({suffix})")
            break

    # 3. Gate-shaped path. A leading /go/ or /redirect is worth more than a
    #    gate word buried at the end of a long slug.
    segs = _segments(path)
    hits = [s for s in segs if _segment_is_gate(s)]
    if hits:
        score += min(2, len(hits))
        if _segment_is_gate(segs[0]):
            score += 1
            reasons.append(f"starts with /{segs[0]}")
        reasons.append("gate path /" + ", /".join(hits[:3]))
    # A short machine-made tail (a code, not a word) is how /go/xyz looks.
    if segs and _SHORT_CODE.match(segs[-1]) and not _WORDY.match(segs[-1]):
        score += 1
        reasons.append("short code in the path")

    # 4. Query: forwarding target, or a machine-made credential.
    q = _query(u)
    if any(k in q for k in REDIRECT_PARAMS) and any(
        str(q[k]).lower().startswith(("http://", "https://", "//")) for k in q if k in REDIRECT_PARAMS
    ):
        score += 3
        reasons.append("carries a target URL")
    if any(k in q for k in OPAQUE_PARAMS) and any(
        _OPAQUE_VALUE.match(str(q[k])) for k in q if k in OPAQUE_PARAMS
    ):
        score += 2
        reasons.append("opaque credential in query")

    # 5. Page evidence, when the caller has it.
    blob = f"{title} {text}".lower()
    if blob.strip():
        for marker in GATE_TEXT:
            if marker in blob:
                score += 3
                reasons.append(f"page says '{marker}'")
                break
        words = len(title.split())
        if words >= 4 and not any(m in title.lower() for m in GATE_TEXT):
            score -= 2
            reasons.append("page title reads like an article")

    # 6. Same host as the hop that produced it: usually the site's own next step.
    if prev_url and host_of(prev_url) == host and host:
        score += 1
        reasons.append("same host as the previous hop")

    # 7. Content shape pulls the score back down.
    wordy = sum(1 for s in segs if _WORDY.match(_EXT.sub("", s.lower())))
    suspicious_q = any(k in q for k in (REDIRECT_PARAMS | OPAQUE_PARAMS))
    if len(segs) >= 2 and wordy >= 1 and not hits and not suspicious_q:
        score -= 3
        reasons.append("looks like a content path")
    if wordy >= 2:
        score -= 1
        reasons.append("several slug segments")

    return Verdict(score >= THRESHOLD, score, reasons)


def decide(
    url: str,
    *,
    next_url: str = "",
    prev_url: str = "",
    known_gate: Optional[bool] = None,
) -> Decision:
    """Final or not - decided by evidence, never by the URL's name.

    ``next_url`` is an address the page actually handed over (a Location, a meta
    refresh target, a button target the resolver could not follow). ``known_gate``
    is the registry's answer: a host that exists to forward. Either one keeps the
    chain alive. Without one, ``url`` is the destination - however suspicious it
    looks, and whatever words its path happens to contain.
    """
    hint = looks_like_waypoint(url, prev_url=prev_url, known_gate=known_gate)

    if next_url and not same_target(next_url, url):
        return Decision(CONTINUE, next_url, "the page hands over another address", hint)

    if known_gate is None:
        try:
            from . import sites

            known_gate = sites.is_gate(url)
        except Exception:  # pragma: no cover - sites always present in the bot
            known_gate = False

    if known_gate and not same_target(url, prev_url):
        return Decision(CONTINUE, url, "the host is a shortener, so it must forward", hint)

    if hint.suspicious:
        return Decision(
            FINAL, url,
            "no further navigation found, so this is the destination "
            f"(it only looks like a waypoint: {hint.why})",
            hint,
        )
    return Decision(FINAL, url, "no further navigation on this page", hint)


def same_target(a: str, b: str) -> bool:
    """Same page, ignoring a trailing slash and the fragment."""
    ua = normalize(a).split("#", 1)[0].rstrip("/")
    ub = normalize(b).split("#", 1)[0].rstrip("/")
    return bool(ua) and ua == ub
