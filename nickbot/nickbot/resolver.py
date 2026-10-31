"""Resolution orchestration: fast path first (async HTTP / site API), browser
only as the last resort.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from . import hops, sites
from .config import Config
from .gates import FastResolver
from .proxyfarm import Farm
from .proxypool import ProxyPool

BROWSER_HELP = ("Selenium/Chromium browser engine is unavailable here. "
                "Check that Chromium and ChromeDriver are installed.")

log = logging.getLogger("nickbot.resolver")


class Engine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        home = str(cfg.linkresolver_home)
        if home not in sys.path:
            sys.path.insert(0, home)

        self.fast = FastResolver(
            timeout=cfg.http_timeout,
            max_hops=cfg.max_hops,
            proxy=cfg.proxy,
        )
        self.pool: Optional[ProxyPool] = (
            ProxyPool() if (cfg.auto_proxy and not cfg.proxy_farm) else None
        )
        self.farm: Optional[Farm] = Farm(country=cfg.proxy_country) if cfg.proxy_farm else None
        self._proxied: Dict[str, FastResolver] = {}
        self.sem = asyncio.Semaphore(max(1, cfg.http_concurrency))
        self.bsem = asyncio.Semaphore(max(1, cfg.browser_concurrency))
        self._cache: Dict[Tuple[str, bool], Tuple[float, Dict[str, Any]]] = {}
        self._inflight: Dict[Tuple[str, bool], asyncio.Future] = {}
        self._cache_ttl = 900.0
        self._cache_max = 512
        self.inflight = 0
        self.waiting = 0
        self.hits = 0
        self.misses = 0
        # Only worth maintaining a proxy pool after a real IP wall; otherwise it
        # is pure bandwidth for nothing.
        self.farm_needed = False

    # ------------------------------------------------------------------ cache

    def _cache_get(self, key: Tuple[str, bool]) -> Optional[Dict[str, Any]]:
        hit = self._cache.get(key)
        if not hit:
            return None
        ts, data = hit
        if time.time() - ts > self._cache_ttl:
            self._cache.pop(key, None)
            return None
        self.hits += 1
        return data

    def _cache_put(self, key: Tuple[str, bool], data: Dict[str, Any]) -> None:
        if len(self._cache) >= self._cache_max:
            oldest = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
            self._cache.pop(oldest, None)
        self._cache[key] = (time.time(), data)

    # --------------------------------------------------------- browser fallback

    def _browser(self, url: str) -> Dict[str, Any]:
        """Browser fallback. A slim install (Termux, Docker without Chromium)
        must not take the whole chain down with it."""
        try:
            from linkresolver.browser import BrowserResolver
        except Exception as exc:  # ImportError, missing system libs, no engine
            return {
                "final": "", "ok": False, "steps": [], "method": "browser",
                "note": f"Browser engine unavailable here ({type(exc).__name__})",
                "missing": True,
            }

        try:
            br = BrowserResolver(
                headless=True,
                timeout=self.cfg.browser_timeout,
                proxy=self.cfg.proxy,
            )
            final, steps, note = br.resolve(url)
        except Exception as exc:
            missing = isinstance(exc, (ImportError, ModuleNotFoundError))
            return {
                "final": "", "ok": False, "method": "browser", "missing": missing,
                "steps": [{"kind": "error", "url": url,
                           "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}],
                "note": BROWSER_HELP if missing else f"Browser run failed: {type(exc).__name__}",
            }

        missing = "not installed" in (note or "").lower() or "unavailable" in (note or "").lower()
        return {
            "final": final or url,
            "ok": bool(final) and final != url,
            "missing": missing,
            "steps": [
                {"kind": getattr(s, "kind", "note"), "url": getattr(s, "url", ""),
                 "status": getattr(s, "status", None),
                 "detail": getattr(s, "detail", "")}
                for s in (steps or [])
            ],
            "note": note or "Browser run finished",
            "method": "browser",
        }

    # ------------------------------------------------------------------- public

    async def resolve(self, url: str, allow_browser: Optional[bool] = None,
                      fresh: bool = False) -> Dict[str, Any]:
        """``fresh=True`` skips the cache - the Retry button would otherwise
        replay the exact failure the user just saw."""
        allow = self.cfg.allow_browser if allow_browser is None else allow_browser
        key = (url, allow)

        cached = None if fresh else self._cache_get(key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            out["elapsed"] = 0.0
            return out

        # Same link already in flight: wait for that result instead of hitting
        # the site twice.
        if key in self._inflight:
            return dict(await asyncio.shield(self._inflight[key]))

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._inflight[key] = fut
        self.misses += 1
        self.waiting += 1
        try:
            await self.sem.acquire()
        except BaseException:
            self.waiting -= 1
            self._inflight.pop(key, None)
            raise
        self.waiting -= 1
        self.inflight += 1

        try:
            data = await self._resolve_inner(url, allow)
        except Exception as exc:
            data = {
                "input": url, "final": url, "ok": False, "method": "",
                "steps": [{"kind": "error", "url": url, "detail": str(exc)[:200]}],
                "elapsed": 0.0, "note": f"{type(exc).__name__}: {exc}", "blocked": "",
            }
        finally:
            self.inflight -= 1
            self.sem.release()
            self._inflight.pop(key, None)

        self._cache_put(key, data)
        if not fut.done():
            fut.set_result(data)
        return dict(data)

    async def _resolve_inner(self, url: str, allow: bool) -> Dict[str, Any]:
        """Walk the chain until the destination is genuinely final.

        The rule, and the whole point of this method: a hop is only continued
        when there is *evidence* of another navigation - the page handed over an
        address, or the chain stopped on a host that exists to forward. A URL is
        never rejected because its path says "auth" or "generate"; a generated
        auth endpoint with nothing behind it is a perfectly good destination.

        The pattern score in :mod:`nickbot.hops` buys exactly one thing: when an
        answer looks like a waypoint, the browser gets one shot at the page that
        produced it, because gates hand HTTP clients a decoy and browsers the
        real exit. Whatever the browser finds after that is the answer.
        """
        t0 = time.perf_counter()
        budget = max(1, int(self.cfg.max_hops))
        cur = url
        seen = {hops.normalize(url)}
        browser_tried: set[str] = set()
        trail: List[Dict[str, Any]] = []
        data: Dict[str, Any] = {}

        for n in range(1, budget + 1):
            log.info("[resolver] hop=%d url=%s", n, cur)
            data = await self._one_hop(cur, allow)
            data["input"] = url
            final = (data.get("final") or "").strip()
            ok = bool(data.get("ok") and final)
            method = data.get("method") or "http"
            log.info("[resolver] method=%s ok=%s next=%s", method, ok, final or "-")

            if not ok:
                trail.append({"hop": n, "url": cur, "next": final, "ok": False,
                              "method": method, "action": "stop", "score": 0, "why": ""})
                data["hops"] = trail
                data["status"] = "blocked" if data.get("blocked") else "unresolved"
                log.info("[resolver] stop: %s", data["status"])
                break

            # Evidence that beats every other rule: the chain arrived at an
            # endpoint that issues a credential. That is the destination, and
            # nothing may be navigated from it - no browser pass, no next hop.
            if hops.is_stop_endpoint(final):
                trail.append({"hop": n, "url": cur, "next": final, "ok": True,
                              "method": method, "action": "final",
                              "score": 0, "why": "auth endpoint (destination)"})
                data["hops"] = trail
                data["final"] = final
                data["status"] = "ok"
                data["note"] = "Reached the auth endpoint - this is the destination"
                log.info("[resolver] auth endpoint reached, stopping: %s", final)
                log.info("[resolver] FINAL=%s", final)
                break

            hint = hops.looks_like_waypoint(final, prev_url=cur)

            # It merely *looks* like a waypoint. One browser pass on the page
            # that produced it, then we accept whatever is there.
            key0 = hops.normalize(cur)
            if hint.suspicious and allow and method != "browser" and key0 not in browser_tried:
                browser_tried.add(key0)
                log.info("[resolver] %s looks like a waypoint (%s) - one browser check",
                         final, hint.why)
                b = await self._one_hop(cur, allow, force_browser=True)
                bfinal = (b.get("final") or "").strip()
                if b.get("ok") and bfinal and not hops.same_target(bfinal, final):
                    log.info("[resolver] browser found %s", bfinal)
                    b["input"] = url
                    data = b
                    final, method = bfinal, b.get("method") or "browser"
                    hint = hops.looks_like_waypoint(final, prev_url=cur)
                elif not b.get("ok") and b.get("blocked"):
                    # The browser hit a human-verification wall; the HTTP answer
                    # stands but must not be sold as verified success.
                    data["note"] = ((data.get("note") + " · ") if data.get("note") else "") + \
                        (b.get("note") or "Browser could not verify the destination")

            decision = hops.decide(
                final,
                next_url=(data.get("next") or ""),
                prev_url=cur,
            )
            trail.append({
                "hop": n, "url": cur, "next": final, "ok": True, "method": method,
                "action": decision.action, "score": hint.score, "why": hint.why,
            })
            data["hops"] = trail
            log.info("[resolver] action=%s score=%d why=%s", decision.action, hint.score, hint.why)

            if not decision.continues:
                data["final"] = final
                data["status"] = "ok"
                if hint.suspicious:
                    log.info("[resolver] looked like a waypoint but nothing follows: %s", final)
                log.info("[resolver] FINAL=%s", final)
                break

            key = hops.normalize(decision.url)
            if key in seen or hops.same_target(decision.url, cur):
                data["ok"] = False
                data["status"] = "loop"
                data["final"] = final
                data["hops"] = trail
                data["input"] = url
                data["note"] = f"Stopped on a page that points back at itself ({decision.reason})"
                log.info("[resolver] stop: loop at %s", final)
                break
            seen.add(key)
            cur = decision.url
        else:
            data["ok"] = False
            data["status"] = "max_hops"
            data["note"] = f"Maximum redirect hops reached ({budget})"
            log.info("[resolver] stop: max_hops")

        data["elapsed"] = time.perf_counter() - t0
        return data

    async def _one_hop(self, url: str, allow: bool, force_browser: bool = False) -> Dict[str, Any]:
        """Resolve a single address: HTTP fast path, browser when it is needed."""
        t0 = time.perf_counter()

        # A credential-issuing endpoint is the destination. Fetching it again -
        # or handing it to a browser - is how the chain used to drift on to the
        # ad page behind it.
        if hops.is_stop_endpoint(url):
            return {
                "input": url, "final": url, "ok": True, "method": "endpoint",
                "steps": [{"kind": "note", "url": url,
                           "detail": "Auth endpoint - this is the destination"}],
                "elapsed": 0.0, "note": "Reached the auth endpoint - this is the destination",
                "blocked": "", "needs_browser": False,
            }

        data = await self.fast.resolve(url)
        data["input"] = url

        # Fast path worked -> done (usually 0.2-2s). A forced browser run is the
        # exception: the caller already decided the HTTP answer is not enough.
        if data.get("ok") and data.get("final") and not force_browser:
            data["elapsed"] = time.perf_counter() - t0
            return data

        # Site blocks our IP: a browser on the same exit IP changes nothing.
        if data.get("blocked") == "vpn":
            self.farm_needed = True
            data["needs_browser"] = False
            data["note"] = "Site blocks datacenter / VPN IPs"
            if self.pool is not None or self.farm is not None:
                got = await self._retry_via_proxy(url, t0)
                if got is not None:
                    return got
            data["elapsed"] = time.perf_counter() - t0
            return data

        heavy = force_browser or data.get("needs_browser") or sites.needs_browser(url)
        if not allow or not heavy:
            data["elapsed"] = time.perf_counter() - t0
            if not allow and heavy:
                data["note"] = ((data.get("note") + " · ") if data.get("note") else "") + \
                    "Browser fallback is disabled (mode=fast)"
            return data

        # Browser fallback
        async with self.bsem:
            b = await asyncio.to_thread(self._browser, data.get("final") or url)

        data["steps"] = (data.get("steps") or []) + (b.get("steps") or [])
        data["method"] = "browser"
        data["elapsed"] = time.perf_counter() - t0

        # Print the trail the browser actually walked: without it, "the gate
        # ended on an ad page" is indistinguishable from "the link is dead".
        walked = [s.get("url", "") for s in (b.get("steps") or [])
                  if s.get("kind") in ("redirect", "click") and s.get("url")]
        if walked:
            log.info("[resolver] browser trail: %s", " -> ".join(dict.fromkeys(walked)))

        # The browser reports where it ended up, and a gate chain that runs
        # gate -> auth endpoint -> ad page ends on the ad page. The auth
        # endpoint is the destination, so read the navigation trail instead of
        # trusting the last URL.
        stop = hops.first_stop_endpoint(b.get("steps") or [])
        if stop:
            data["ok"] = True
            data["final"] = stop
            data["note"] = "Browser passed through the auth endpoint - that is the destination"
            return data

        if b.get("ok"):
            data["ok"] = True
            data["final"] = b["final"]
            data["note"] = b.get("note") or "Resolved with browser"
        else:
            data["ok"] = False
            data["final"] = data.get("final") or url
            data["note"] = b.get("note") or "Browser could not pass the gate"
            if b.get("missing"):
                data["browser_missing"] = True
                data["method"] = "http"
        return data

    def _resolver_for(self, px: str, timeout: float = 10.0) -> FastResolver:
        r = self._proxied.get(px)
        if r is None:
            r = FastResolver(
                timeout=timeout,
                max_hops=self.cfg.max_hops,
                proxy=px,
            )
            self._proxied[px] = r
        return r

    async def _retry_via_proxy(self, url: str, t0: float) -> Optional[Dict[str, Any]]:
        """The site blocks our IP - run it again from an exit that can pass."""
        pool = self.farm or self.pool
        if pool is None:
            return None
        # Empty farm: refill in the background, don't make this request wait.
        if self.farm is not None and not self.farm.stats().get("total"):
            asyncio.create_task(self.farm.refresh(target=url, limit=200, budget=40))
            return None
        deadline = time.perf_counter() + max(4.0, self.cfg.proxy_budget)
        tried: set[str] = set()
        while time.perf_counter() < deadline - 1.0:
            left = deadline - time.perf_counter()
            px = await pool.find_for(url, budget=min(4.0, left), exclude=tried)
            if not px:
                break
            tried.add(px)
            try:
                d2 = await asyncio.wait_for(
                    self._resolver_for(px, timeout=6.0).resolve(url),
                    timeout=max(3.0, deadline - time.perf_counter()),
                )
            except asyncio.TimeoutError:
                pool.drop(px)
                continue
            if d2.get("ok") and d2.get("final"):
                d2["input"] = url
                tag = f"via {self.farm.country} proxy" if self.farm else "via rotating proxy"
                d2["note"] = ((d2.get("note") or "") + f" · {tag}").strip(" ·")
                d2["elapsed"] = time.perf_counter() - t0
                d2["blocked"] = ""
                d2["proxy"] = px
                return d2
            pool.drop(px)
        return None

    def set_proxy(self, px: str) -> None:
        """Switch the exit proxy at runtime (and clear the cache so failed
        results are not replayed)."""
        self.cfg.proxy = (px or "").strip()
        self.fast = FastResolver(
            timeout=self.cfg.http_timeout,
            max_hops=self.cfg.max_hops,
            proxy=self.cfg.proxy,
        )
        self._proxied.clear()
        self._cache.clear()

    def set_farm(self, on: bool, country: str = "") -> None:
        """Toggle the country-verified proxy farm; changing country loads that
        country's pool file."""
        if country:
            self.cfg.proxy_country = country.strip().upper()[:2]
        self.cfg.proxy_farm = on
        self.farm = Farm(country=self.cfg.proxy_country) if on else None
        self._cache.clear()

    def load(self) -> Tuple[int, int]:
        return self.inflight, self.waiting

    def cache_info(self) -> Tuple[int, int]:
        return len(self._cache), self.hits
