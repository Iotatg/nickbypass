"""Resolution engine: fast HTTP path (manual hop-by-hop follow) + browser fallback."""

from __future__ import annotations

import re
import time
import urllib.parse as up
from typing import Dict, List, Optional, Tuple

import httpx

from .models import Result, Step
from . import classify
from . import rules as R

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BLOCK_MARKERS = (
    "just a moment",
    "attention required",
    "cf-chl",
    "checking your browser",
    "enable javascript and cookies to continue",
    "ddos protection by",
    "please verify you are a human",
    "g-recaptcha",
    "h-captcha",
)


def normalize(url: str) -> str:
    url = (url or "").strip().strip("'\"<>")
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def headers_for(url: str, referer: Optional[str] = None) -> Dict[str, str]:
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site" if referer else "none",
        "Sec-Fetch-User": "?1",
    }
    if referer:
        h["Referer"] = referer
    return h


def is_blocked(status: int, body: str) -> Tuple[bool, str]:
    if status in (403, 429, 503):
        low = body[:200000].lower()
        for m in BLOCK_MARKERS:
            if m in low:
                return True, f"HTTP {status} + anti-bot marker '{m}'"
        return True, f"HTTP {status} (refused / rate limited)"
    low = body[:200000].lower()
    for m in BLOCK_MARKERS:
        if m in low and len(low) < 120000:
            return True, f"Anti-bot / verification page '{m}'"
    return False, ""


class Resolver:
    def __init__(
        self,
        timeout: float = 20.0,
        max_hops: int = 12,
        allow_browser: bool = True,
        headless: bool = True,
        browser_timeout: float = 30.0,
        force_browser: bool = False,
    ) -> None:
        self.timeout = timeout
        self.max_hops = max_hops
        self.allow_browser = allow_browser
        self.force_browser = force_browser
        self.headless = headless
        self.browser_timeout = browser_timeout

    # ------------------------------------------------------------ public

    def resolve(self, raw_url: str) -> Result:
        """Follow the chain until the destination is genuinely final.

        Evidence decides, not the URL's name. A hop continues only when the page
        handed over another address, or when it stopped on a host that exists to
        forward. ``auth.*.workers.dev/generate?code=`` is a perfectly normal
        destination when nothing follows it - the old code rejected it on sight
        and kept walking past the real answer.

        The pattern score buys one thing only: when an answer merely *looks* like
        a waypoint, the browser gets one pass at the page that produced it, since
        gates hand HTTP clients a decoy and browsers the real exit.
        """
        res = self._resolve_once(raw_url)
        seen = {normalize(raw_url)}
        tried: set[str] = set()
        cur = raw_url

        for hop in range(1, self.max_hops + 1):
            if not (res.ok and res.final_url):
                break
            nxt = res.final_url
            prev = cur
            if classify.is_stop_endpoint(nxt):
                res.steps.append(Step("note", nxt,
                                      detail="Auth endpoint - final, nothing is navigated from it"))
                break
            hint = classify.looks_like_waypoint(
                nxt, prev_url=prev,
                known_gate=R.matches(R.host_of(nxt), R.NEEDS_BROWSER | R.PLAIN_SHORTENERS),
            )

            if (hint.suspicious and self.allow_browser and res.method != "browser"
                    and normalize(prev) not in tried):
                tried.add(normalize(prev))
                res.steps.append(Step(
                    "note", nxt,
                    detail=f"Looks like a waypoint ({hint.why}) - one browser pass on the page that produced it",
                    extra={"score": hint.score},
                ))
                b = self._resolve_once(prev, force_browser=True)
                if b.ok and b.final_url and normalize(b.final_url) != normalize(nxt):
                    res.steps.extend(b.steps)
                    res.final_url = b.final_url
                    res.method = b.method or "browser"
                    res.ok = b.ok
                    res.note = b.note
                    nxt = res.final_url
                    hint = classify.looks_like_waypoint(
                        nxt, prev_url=prev,
                        known_gate=R.matches(R.host_of(nxt), R.NEEDS_BROWSER | R.PLAIN_SHORTENERS),
                    )

            decision = classify.decide(
                nxt, prev_url=prev,
                known_gate=R.matches(R.host_of(nxt), R.NEEDS_BROWSER | R.PLAIN_SHORTENERS),
            )
            res.steps.append(Step(
                "note", nxt,
                detail=f"Hop {hop}: {decision.action} - {decision.reason}",
                extra={"action": decision.action, "score": hint.score},
            ))
            res.final_url = decision.url

            if not decision.continues:
                break
            if normalize(decision.url) in seen:
                res.ok = False
                res.note = (f"Loop: {R.host_of(decision.url)} points back to a page already visited")
                break
            seen.add(normalize(decision.url))
            cur = decision.url
            follow = self._resolve_once(cur)
            res.steps.extend(follow.steps)
            res.final_url = follow.final_url or cur
            res.ok = follow.ok
            res.method = follow.method or res.method
            res.note = follow.note
            res.blocked = res.blocked or follow.blocked
        else:
            res.ok = False
            res.note = f"Maximum redirect hops reached ({self.max_hops})"

        return res

    def _resolve_once(self, raw_url: str, force_browser: Optional[bool] = None) -> Result:
        t0 = time.time()
        url = normalize(raw_url)
        res = Result(input_url=raw_url)

        if not url:
            res.note = "Empty URL"
            return res

        # A credential-issuing endpoint is the destination: nothing is fetched
        # past it, because the pages that look like they follow are the ad layer.
        if classify.is_stop_endpoint(url):
            res.final_url = url
            res.method = "endpoint"
            res.ok = True
            res.note = "Reached the auth endpoint - this is the destination"
            res.steps.append(Step("note", url, detail="Auth endpoint, nothing to follow"))
            res.elapsed = time.time() - t0
            return res

        force = self.force_browser if force_browser is None else force_browser

        # Forced browser mode
        if force and self.allow_browser:
            from .browser import BrowserResolver

            res.steps.append(Step("note", url, detail="Forced browser mode, skipping the HTTP fast path"))
            b_url, b_steps, b_note = BrowserResolver(
                headless=self.headless, timeout=self.browser_timeout
            ).resolve(url)
            res.steps.extend(b_steps)
            res.final_url = b_url or url
            res.method = "browser"
            res.ok = bool(b_url) and b_url != url
            res.note = b_note
            res.elapsed = time.time() - t0
            return res

        client = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            headers=headers_for(url),
            verify=False,
            http2=False,
        )
        visited: set[str] = set()
        referer: Optional[str] = None
        needs_browser = False

        try:
            for _hop in range(self.max_hops):
                if url in visited:
                    res.steps.append(Step("note", url, detail="Redirect loop detected, stopping"))
                    break
                visited.add(url)

                if referer:
                    client.headers.update({"Referer": referer})

                t = time.time()
                try:
                    resp = client.get(url)
                except Exception as exc:
                    res.steps.append(
                        Step("error", url, detail=f"Request failed: {type(exc).__name__}: {exc}", elapsed=time.time() - t)
                    )
                    res.blocked = True
                    res.note = "Request failed (unreachable host / timeout / TLS)"
                    break

                ctype = resp.headers.get("content-type", "")
                body = resp.text if "html" in ctype or "text" in ctype else ""
                res.steps.append(
                    Step(
                        "request",
                        url,
                        status=resp.status_code,
                        detail=f"{ctype.split(';')[0] or 'unknown'} · {len(resp.content)} B",
                        elapsed=time.time() - t,
                    )
                )

                # 1) redirect
                if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location", "")
                    nxt = up.urljoin(url, loc) if loc else ""
                    if not nxt:
                        res.note = "Redirect without a Location header"
                        break
                    res.steps.append(Step("redirect", nxt, status=resp.status_code, detail=f"HTTP {resp.status_code}"))
                    if classify.is_stop_endpoint(nxt):
                        res.steps.append(Step("note", nxt,
                                              detail="Redirects to an auth endpoint - that is the destination"))
                        res.final_url = nxt
                        res.method = "endpoint"
                        res.ok = True
                        res.note = "Reached the auth endpoint - this is the destination"
                        break
                    referer, url = url, nxt
                    continue

                # 2) anti-bot / verification
                blocked, why = is_blocked(resp.status_code, body)
                if blocked:
                    res.steps.append(Step("blocked", url, status=resp.status_code, detail=why))
                    res.blocked = True
                    needs_browser = True
                    res.note = why
                    break

                # 3) not HTML: treat it as the destination (file / image / API)
                if body == "":
                    res.final_url = url
                    res.method = "http"
                    res.ok = True
                    res.note = res.note or f"Returned {ctype.split(';')[0] or 'binary'} directly, not a redirect page"
                    break

                # 4) look for the next hop inside the page
                found = R.extract_next(url, body)
                if found:
                    nxt, why, _fields = found
                    nxt = normalize(nxt)
                    if nxt and nxt != url:
                        if classify.is_stop_endpoint(nxt):
                            res.steps.append(Step("note", nxt,
                                                  detail="Page points at an auth endpoint - that is the destination"))
                            res.final_url = nxt
                            res.method = "endpoint"
                            res.ok = True
                            res.note = "Reached the auth endpoint - this is the destination"
                            break
                        res.steps.append(Step("extract", nxt, detail=why))
                        referer, url = url, nxt
                        continue

                # 5) nothing to extract: destination, or still stuck on the gate?
                host = R.host_of(url)
                input_host = R.host_of(normalize(raw_url))
                low = body.lower()

                # Gate score: timers + countdown copy + continue button + script
                score = 0
                if "settimeout" in low or "setinterval" in low:
                    score += 2
                if re.search(r"\b\d{1,2}\s*(?:s|sec|secs|second|seconds)\b", body, re.I):
                    score += 2
                if any(k in low for k in ("countdown", "verifying", "please wait", "generating",
                                          "continue", "get link", "just a moment")):
                    score += 1
                if "<script" in low:
                    score += 1
                looks_jsy = score >= 3

                stayed = host == input_host or R.matches(host, R.NEEDS_BROWSER)

                if R.matches(host, R.NEEDS_BROWSER) or (stayed and looks_jsy):
                    needs_browser = True
                    res.steps.append(
                        Step(
                            "note",
                            url,
                            detail="Page needs a JS timer / click before it reveals the destination"
                            + (" (domain is on the known browser-only list)" if R.matches(host, R.NEEDS_BROWSER) else ""),
                        )
                    )
                    res.note = R.HINTS.get(host, "Needs a browser to run the JavaScript that builds the destination")
                elif stayed:
                    # Still on the shortener's own page: unresolved (dead link, error page, login wall)
                    res.final_url = url
                    res.method = "http"
                    res.ok = False
                    res.note = "Still on the shortener's own page, no destination found (link may be dead or need a login)"
                else:
                    res.final_url = url
                    res.method = "http"
                    res.ok = True
                    res.note = res.note or "Reached the destination (nothing left to follow)"
                break

            # 6) browser fallback
            if needs_browser and self.allow_browser:
                from .browser import BrowserResolver

                br = BrowserResolver(headless=self.headless, timeout=self.browser_timeout)
                b_url, b_steps, b_note = br.resolve(url)
                res.steps.extend(b_steps)
                res.method = "browser"
                if b_url:
                    res.final_url = b_url
                    res.ok = b_url != normalize(raw_url)
                    res.note = b_note or "Resolved in browser mode"
                else:
                    res.final_url = url
                    res.ok = False
                    res.note = b_note or "Browser mode did not reach the destination either (login / captcha / region block)"
            elif needs_browser and not self.allow_browser:
                res.note = ((res.note + " · ") if res.note else "") + "This page needs browser mode (add --browser, or tick the box in the web UI)"
        finally:
            client.close()

        if not res.final_url and res.method == "":
            res.final_url = url
            res.ok = False

        res.elapsed = time.time() - t0
        return res
