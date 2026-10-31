"""Fast async resolver.

Why this exists: the old path opened a brand-new sync HTTP client for every
link (fresh TLS handshake, no keep-alive) and then fell back to a full
Chromium boot. That is why a 4-hop shortlink took 30s.

This module does three things:

1. One shared ``httpx.AsyncClient`` with keep-alive, so hop #2..N cost ~1ms
   instead of a new handshake. HTTP/2 is used only when the ``h2`` package is
   actually installed — a missing optional dependency must not break resolving.
2. Site adapters for the "GT Links" family (vplink / gplinks / arolinks /
   liteshort ...): those sites expose ``POST /links/go`` returning
   ``{"status":"success","url":"https://..."}``. One request instead of a
   browser = sub-second bypass.
3. Honest failure states: if the site answers with a "VPN detected" wall we
   say so immediately instead of burning 30s in a browser that can never
   pass (same IP, same block).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx

# The resolver engine (linkresolver) lives next to the project by default;
# LINKRESOLVER_HOME overrides it.
_HOME = Path(
    os.getenv("LINKRESOLVER_HOME")
    or (Path(__file__).resolve().parent.parent.parent / "linkresolver")
)
if str(_HOME) not in sys.path:
    sys.path.insert(0, str(_HOME))

from . import hops  # noqa: E402  (credential endpoints are destinations)

try:
    from linkresolver import normalize  # noqa: E402
    from linkresolver.rules import extract_next, host_of  # noqa: E402
except ModuleNotFoundError as _exc:  # pragma: no cover - deploy-time hint
    raise ModuleNotFoundError(
        f"resolver engine not importable from {_HOME}. Clone the whole repo "
        "(nickbot/ + linkresolver/ side by side) or set LINKRESOLVER_HOME."
    ) from _exc

try:  # HTTP/2 is a nice-to-have, never a requirement
    import h2 as _h2  # noqa: F401

    HAS_H2 = True
except Exception:  # pragma: no cover - depends on the machine
    HAS_H2 = False

# ---------------------------------------------------------------------- constants

UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# The site explicitly refuses datacenter / VPN IPs.
VPN_MARKERS = (
    "vpn detected",
    "disable vpn",
    "turn off vpn",
    "turn off/ disable vpn",
    "you're using a vpn",
    "you are using a vpn",
    "vpn or proxy",
    "please disable your vpn",
    "proxy detected",
)

# Cloudflare / human verification
CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "enable javascript and cookies to continue",
    "attention required",
    "error code: 1020",
    "error code: 1000",
)

# Sites built on the same template ("GT Links"): all of them have POST /links/go.
# The authoritative list lives in registry.py (kind="gt"); this is only a
# fallback in case the registry cannot be imported.
GT_FAMILY_FALLBACK = (
    "vplink", "gplinks", "arolinks", "liteshort", "inshorturl",
    "vipshort", "cpmshort", "earn4link", "earnlink", "shrinkme",
    "tii.la", "tpi.li", "oii.la", "clk.sh", "za.gl",
)

# Pure assets: never worth a hop.
ASSET_EXT = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".mp4", ".mp3",
)

# The GT template checks CSRF with a double-submit cookie: the request has to
# carry a `csrfToken` cookie AND an `X-CSRF-TOKEN` header with the same value.
# Any value passes, which is why we can mint one instead of scraping the page.
CSRF_COOKIE = "csrfToken"
CSRF_HEADER = "X-CSRF-TOKEN"


def _is_gt(host: str) -> bool:
    """Is this the shared PHP shortener template (POST /links/go)?"""
    h = (host or "").lower()
    try:
        from . import sites as _sites

        s = _sites.lookup("https://" + h + "/")
        if s is not None:
            return s.kind == _sites.KIND_GT
    except Exception:
        pass
    return any(g in h for g in GT_FAMILY_FALLBACK)


def _slug_of(url: str) -> str:
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return parts[-1] if parts else ""


def _origin(url: str) -> str:
    s = urlsplit(url)
    return f"{s.scheme}://{s.netloc}"


def _vpn_wall(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in VPN_MARKERS)


def _gate_score(html: str) -> int:
    """Score the ad-gate signals: countdown, timer, continue button, script."""
    low = html.lower()
    score = 0
    if "settimeout" in low or "setinterval" in low:
        score += 2
    if re.search(r"\b\d{1,2}\s*(?:s|sec|secs|second|seconds)\b", html, re.I):
        score += 2
    if any(k in low for k in ("countdown", "verifying", "please wait", "generating link",
                              "generating your link", "opening link", "continue",
                              "get link", "just a moment", "landing..")):
        score += 1
    if "<script" in low:
        score += 1
    return score


def _countdown(html: str) -> int:
    """Seconds the page asks the visitor to wait, 0 when there is no timer."""
    best = 0
    patterns = (
        r"(?:var|let|const)\s+(?:timeleft|time_left|timeLeft|counter|seconds|timer|count)\s*=\s*(\d{1,3})",
        r"data-(?:seconds|timer|time|count|wait)\s*=\s*[\"']?(\d{1,3})",
        r"id=[\"'](?:timer|countdown|count|counter)[\"'][^>]*>\s*(\d{1,3})",
        r"(\d{1,3})\s*(?:seconds|secs)\b",
    )
    for p in patterns:
        for m in re.finditer(p, html, re.I):
            try:
                best = max(best, int(m.group(1)))
            except ValueError:
                continue
    return min(best, 30)


def _challenge(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in CHALLENGE_MARKERS) and len(low) < 200_000


# A shortener admitting the link is broken. Only checked when the domain really
# is a shortener, so a normal content page can never be misread.
DEAD_MARKERS = (
    "there might be a problem with the requested link",
    "this link has expired",
    "link has expired",
    "link you requested",
    "this link is invalid",
    "this link is no longer",
    "link not found",
    "url not found",
    "invalid or expired",
    "this url is invalid",
)


def _site_of(url: str):
    try:
        from . import sites as _sites

        return _sites.lookup(url)
    except Exception:
        return None


def _dead_link(html: str, url: str) -> bool:
    """The shortener's own "invalid / expired" page is not a resolution."""
    if _site_of(url) is None:
        return False
    low = html.lower()
    return any(m in low for m in DEAD_MARKERS)


# ---------------------------------------------------------------------- engine


# Ad-chain hosts: waypoints, never destinations. If we end up on one of these
# the honest answer is "unresolved", otherwise users get a pipefitter.in ad page
# presented as their real link.
AD_HOSTS = {
    "pipefitter.in",
    "adf.ly", "adfoc.us", "shorte.st", "shrinkme.io", "shrinkearn.com",
    "ouo.io", "ouo.press", "linkvertise.com", "linkvertise.net",
    "gplinks.co", "gplink.in", "vplink.in", "vplinks.in", "arolinks.com",
    "liteshort.co", "inshorturl.com", "earn4link.in", "linkpays.in",
    "mdiskshortner.link", "clicksfly.com", "dropgalaxy.com",
}


def _is_terminal(cur: str, start_host: str) -> bool:
    """Does ``cur`` look like a destination rather than an ad-chain waypoint?"""
    h = host_of(cur)
    if not h or h in AD_HOSTS:
        return False
    # The shortener's own front page is never the destination.
    if h == start_host and not urlsplit(cur).path.strip("/"):
        return False
    try:
        from . import sites as _sites

        # Pass the full URL: a generated-list domain only counts as a gate when
        # the path looks like a short code, so a deep path on the same host (a
        # landing page) still counts as a destination.
        if _sites.is_gate(cur):
            return False
    except Exception:
        pass
    return True


class FastResolver:
    """Async resolver sharing one connection pool."""

    def __init__(
        self,
        timeout: float = 12.0,
        max_hops: int = 12,
        proxy: str = "",
    ) -> None:
        self.timeout = timeout
        self.max_hops = max_hops
        self.proxy = proxy or ""
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- client

    def _client_kwargs(self) -> Dict[str, Any]:
        # Proxied handshakes are slower; give connect its own budget.
        connect = 10.0 if self.proxy else 6.0
        kwargs: Dict[str, Any] = dict(
            timeout=httpx.Timeout(self.timeout, connect=connect),
            follow_redirects=False,
            verify=False,
            # Cheap HTTP proxies usually cannot do h2 CONNECT, and httpx raises
            # ImportError when http2=True without the optional `h2` package.
            http2=HAS_H2 and not bool(self.proxy),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
            headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    async def client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(**self._client_kwargs())
        return self._client

    async def _isolated_client(self) -> httpx.AsyncClient:
        """A client with its own cookie jar.

        The shared client would leak one visitor's session cookies into the
        next request; gate sites hand out per-link cookies, so that matters.
        """
        return httpx.AsyncClient(**self._client_kwargs())

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ main

    async def resolve(self, raw_url: str) -> Dict[str, Any]:
        t0 = time.perf_counter()
        url = normalize(raw_url)
        out: Dict[str, Any] = {
            "input": raw_url,
            "final": "",
            "ok": False,
            "method": "http",
            "steps": [],
            "elapsed": 0.0,
            "note": "",
            "blocked": "",
            "needs_browser": False,
        }
        if not url:
            out["note"] = "Empty or invalid URL"
            return out

        # An auth endpoint is the destination. Not fetched, not walked through:
        # the pages that look like they follow it are the ad layer.
        if hops.is_stop_endpoint(url):
            out["final"] = url
            out["ok"] = True
            out["method"] = "endpoint"
            out["note"] = "Reached the auth endpoint - this is the destination"
            out["steps"].append({"kind": "note", "url": url,
                                 "detail": "Auth endpoint, nothing to follow"})
            return out

        steps: List[Dict[str, Any]] = out["steps"]
        start_host = host_of(url)
        cur = url
        visited: set[str] = set()
        # Deepest address we reached that really looked like a destination.
        # Ad chains often bounce back to a waypoint at the end; in that case the
        # last good address is the answer, not the ad page.
        best_dest = ""
        # True only when the current URL came from a 30x the site itself sent;
        # a hop scraped out of an ad page's HTML proves nothing about the target.
        via_redirect = False
        own_client: Optional[httpx.AsyncClient] = None

        try:
            if _is_gt(start_host):
                # Gate sites: isolate cookies, and never trust links scraped
                # from their HTML (those are the "sponsored" decoys).
                own_client = await self._isolated_client()
                client = own_client
            else:
                client = await self.client()

            for _ in range(self.max_hops):
                if cur in visited:
                    steps.append({"kind": "note", "url": cur, "detail": "Redirect loop detected"})
                    break
                visited.add(cur)

                try:
                    resp = await client.get(
                        cur,
                        headers={"User-Agent": UA_MOBILE, "Referer": url if cur != url else ""},
                    )
                except httpx.HTTPError as exc:
                    steps.append({"kind": "error", "url": cur, "detail": f"{type(exc).__name__}: {exc}"})
                    if _is_terminal(cur, start_host):
                        # We already know the destination; it just doesn't answer.
                        out["final"] = cur
                        out["ok"] = True
                        out["note"] = f"Destination not reachable ({type(exc).__name__})"
                    else:
                        out["note"] = f"Request failed: {type(exc).__name__}"
                        out["final"] = cur
                    break

                loc = resp.headers.get("location")
                if resp.status_code in (301, 302, 303, 307, 308) and loc:
                    nxt = urljoin(cur, loc)
                    steps.append({"kind": "redirect", "url": cur, "status": resp.status_code,
                                  "detail": "HTTP redirect"})
                    if hops.is_stop_endpoint(nxt):
                        steps.append({"kind": "note", "url": nxt,
                                      "detail": "Redirects to an auth endpoint - that is the destination"})
                        out["final"] = nxt
                        out["ok"] = True
                        out["method"] = "endpoint"
                        out["note"] = "Reached the auth endpoint - this is the destination"
                        break
                    # The destination bounced us back into the ad layer: stop
                    # here and report the previous hop.
                    if host_of(nxt) in AD_HOSTS and _is_terminal(cur, start_host):
                        steps.append({"kind": "note", "url": nxt,
                                      "detail": "Ad layer bounce ignored"})
                        out["final"] = cur
                        out["ok"] = True
                        out["note"] = "Reached destination (ad layer bounced)"
                        break
                    via_redirect = True
                    cur = nxt
                    continue

                if resp.status_code >= 400:
                    # 403/451 + a very short body = IP-level refusal (datacenter
                    # range); only a different exit IP can help.
                    body = (resp.text or "")[:600].lower()
                    ip_blocked = resp.status_code in (403, 451) and (
                        len(resp.text or "") < 400
                        or "forbidden" in body
                        or "access denied" in body
                    )
                    if ip_blocked and not _is_terminal(cur, start_host):
                        steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                      "detail": f"IP refused (HTTP {resp.status_code})"})
                        out["blocked"] = "vpn"
                        out["note"] = (
                            f"Site refuses this IP (HTTP {resp.status_code}) — datacenter / VPN range."
                        )
                        out["final"] = cur
                        break
                    steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                  "detail": "HTTP error"})
                    if _is_terminal(cur, start_host):
                        # We reached the destination; it is the destination that is broken.
                        out["final"] = cur
                        out["ok"] = True
                        out["note"] = f"Destination returned HTTP {resp.status_code} (link may be dead)"
                    else:
                        out["note"] = f"Site returned HTTP {resp.status_code}"
                        out["final"] = cur
                    break

                html = resp.text or ""

                # 0) The shortener itself says the link is invalid / expired.
                if _dead_link(html, cur):
                    steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                  "detail": "Shortener reports an invalid / expired link"})
                    out["final"] = cur
                    out["note"] = ("The shortener itself says this link is invalid or expired — "
                                   "nothing to unpack.")
                    break

                # Remember the deepest address that really looked like a destination.
                if _is_terminal(cur, start_host):
                    best_dest = cur

                # 1) VPN / datacenter IP wall
                if _vpn_wall(html):
                    steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                  "detail": "Site shows 'VPN detected' wall"})
                    out["blocked"] = "vpn"
                    out["note"] = (
                        "This site blocks datacenter / VPN IPs. It refuses the server's IP, "
                        "so no amount of retries will work from here — a residential proxy is required."
                    )
                    break

                # 2) Cloudflare challenge
                if _challenge(html):
                    steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                  "detail": "Anti-bot / Cloudflare challenge"})
                    out["needs_browser"] = True
                    out["note"] = "Anti-bot challenge — needs a real browser"
                    break

                # 3) Site API (GT Links family): one POST and the answer is JSON.
                if _is_gt(start_host) or "/links/go" in html:
                    target, api_msg, api_err = await self._gate_json(client, cur, html)
                    if target:
                        steps.append({"kind": "extract", "url": cur, "detail": "Site API /links/go"})
                        out["final"] = target
                        out["method"] = "gate"
                        out["ok"] = True
                        out["note"] = api_msg or "Resolved via site API"
                        break
                    if api_err:
                        steps.append({"kind": "note", "url": cur, "detail": f"Site API said: {api_err}"})
                        out["note"] = f"Site API refused: {api_err}"
                        if "captcha" in api_err.lower():
                            out["needs_browser"] = True
                    if _is_gt(start_host):
                        # Never fall through to scraped links on a gate page:
                        # those are the sponsored decoys that produce wrong answers.
                        steps.append({"kind": "note", "url": cur,
                                      "detail": "Gate page: only the site API is trusted"})
                        out["needs_browser"] = True
                        out["note"] = ("Still on the shortener page — the site API did not hand "
                                       "over a destination")
                        out["final"] = cur
                        break

                # 4) Generic extraction: meta refresh / JS location / base64 / form / button
                nxt = extract_next(cur, html)
                if nxt:
                    target, detail, form = nxt
                    if target and target != cur:
                        if host_of(target) in AD_HOSTS:
                            # An ad page pointing at another ad page: stop, don't
                            # dress it up as the destination.
                            steps.append({"kind": "note", "url": target,
                                          "detail": "Ad waypoint ignored"})
                            out["needs_browser"] = True
                            out["note"] = "The chain stopped on an ad page that never points anywhere real."
                            out["final"] = best_dest or cur
                            break
                        if hops.is_stop_endpoint(target):
                            steps.append({"kind": "note", "url": target,
                                          "detail": "Page points at an auth endpoint - that is the destination"})
                            out["final"] = target
                            out["ok"] = True
                            out["method"] = "endpoint"
                            out["note"] = "Reached the auth endpoint - this is the destination"
                            break
                        steps.append({"kind": "extract", "url": cur, "detail": detail or "Extracted next hop"})
                        if form:
                            try:
                                r2 = await client.post(target, data=form,
                                                       headers={"User-Agent": UA_MOBILE, "Referer": cur})
                                loc2 = r2.headers.get("location")
                                if loc2:
                                    cur = urljoin(target, loc2)
                                    steps.append({"kind": "redirect", "url": target, "status": r2.status_code,
                                                  "detail": "Form submitted"})
                                    continue
                                target = str(r2.url)
                            except httpx.HTTPError:
                                pass
                        via_redirect = False
                        cur = target
                        continue

                # 5) No next hop. Is this a destination, or are we still on the gate?
                if not _is_terminal(cur, start_host):
                    still_gate = False
                    try:
                        from . import sites as _sites

                        still_gate = _sites.is_gate(cur)
                    except Exception:
                        pass

                    heavy = True
                    try:
                        from . import sites as _sites

                        heavy = _sites.needs_browser(cur)
                    except Exception:
                        pass

                    steps.append({"kind": "note", "url": cur,
                                  "detail": "No destination in the HTML"})
                    out["needs_browser"] = heavy or still_gate
                    if host_of(cur) == start_host:
                        out["note"] = ("Still on the shortener page — the destination is built by "
                                       "JavaScript, not in the HTML")
                    else:
                        out["note"] = "The chain stopped on an ad page that never points anywhere real."
                    out["final"] = best_dest or cur
                    break

                out["final"] = cur
                out["ok"] = True
                out["note"] = "Reached destination (no further redirect)"
                break

            # Last resort: we ended on an ad host but passed a real destination earlier.
            if host_of(out.get("final", "")) in AD_HOSTS and best_dest:
                out["final"] = best_dest
                out["ok"] = True
                out["note"] = "Reached destination (ad layer bounced)"

            if not out["final"] and out["method"]:
                out["final"] = cur

        except Exception as exc:  # a resolver crash must never take the bot down
            out["note"] = f"Resolver error: {type(exc).__name__}: {exc}"
            steps.append({"kind": "error", "url": cur, "detail": str(exc)[:200]})
        finally:
            if own_client is not None:
                try:
                    await own_client.aclose()
                except Exception:
                    pass

        out["elapsed"] = time.perf_counter() - t0
        return out

    # ------------------------------------------------------------- site API

    async def _gate_json(
        self, client: httpx.AsyncClient, page_url: str, html: str
    ) -> Tuple[str, str, str]:
        """POST {origin}/links/go -> {"status":"success","url":"https://..."}

        The template wants three things before it hands the link over:

        * a session (the cookies from the GET above),
        * a CSRF pair — ``csrfToken`` cookie plus a matching ``X-CSRF-TOKEN``
          header (double submit, any value passes),
        * and the countdown to have run out. Until then it answers
          ``{"url":"#"}`` instead of the destination.
        """
        origin = _origin(page_url)
        slug = _slug_of(page_url)
        host = host_of(page_url)
        if not slug:
            return "", "", ""

        tokens = re.findall(r'name=["\'](_?token|_csrf)["\'][^>]*value=["\']([^"\']+)', html, re.I)
        csrf = tokens[0][1] if tokens else uuid.uuid4().hex

        # Make sure the double-submit pair is present in the jar and the headers.
        try:
            client.cookies.set(CSRF_COOKIE, csrf, domain=host, path="/")
        except Exception:
            pass

        headers = {
            "User-Agent": UA_MOBILE,
            "Referer": page_url,
            "Origin": origin,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            CSRF_HEADER: csrf,
        }

        payloads: List[Dict[str, str]] = [
            {"link": slug, "_token": csrf},
            {"link": slug},
            {"link": slug, "_token": csrf, "adType": "1"},
        ]

        wait = _countdown(html)
        last_err = ""
        not_ready = False
        for attempt in range(3):
            stop = False
            for data in payloads:
                if stop:
                    break
                for path in ("/links/go", "/links/go/"):
                    try:
                        r = await client.post(origin + path, data=data, headers=headers)
                    except httpx.HTTPError as exc:
                        last_err = f"{type(exc).__name__}"
                        continue
                    try:
                        j = r.json()
                    except Exception:
                        continue
                    if not isinstance(j, dict):
                        continue

                    target = (j.get("url") or j.get("link") or "").strip()
                    msg = str(j.get("message") or "")

                    if target.startswith("http") and host_of(target) != host:
                        return target, msg, ""

                    # "url": "#" means "not yet" (or "no destination"), never success.
                    if target == "#" or (not target and str(j.get("status")).lower() == "success"):
                        not_ready = True
                    if str(j.get("status")).lower() == "error" and msg:
                        last_err = msg
                        # A token complaint means this payload shape was wrong:
                        # try the next one. Anything else is a real answer.
                        if "csrf" in msg.lower() or "token" in msg.lower():
                            break
                        stop = True
                        break

            # The API answered, just not with a link yet: let the countdown run.
            if attempt < 2 and not stop:
                if wait:
                    await asyncio.sleep(min(wait + 1, 20))
                    wait = 0
                    continue
                if not_ready:
                    # No timer in the HTML but the gate still says "not yet":
                    # some templates start their clock on the first POST.
                    await asyncio.sleep(5)
                    continue
            break

        return "", "", last_err
