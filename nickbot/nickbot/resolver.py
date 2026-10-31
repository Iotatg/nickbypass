"""解析编排：快路径（异步 HTTP / 站点 API）优先，浏览器只做最后兜底。"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

from . import sites
from .config import Config
from .gates import FastResolver
from .proxyfarm import Farm
from .proxypool import ProxyPool


# 引擎（linkresolver）内部的部分提示是中文的，机器人统一输出英文
_TR = (
    (r"^未安装 playwright$", "Playwright not installed"),
    (r"^浏览器启动失败$", "Browser failed to start"),
    (r"^浏览器模式拿到终点$", "Resolved with browser"),
    (r"^浏览器跑完仍未离开广告门.*$",
     "Browser finished but never left the ad gate (captcha / login / region block, or dead link)"),
    (r"^Playwright 不可用：(.*)$", r"Playwright unavailable: \1"),
    (r"^Chromium 启动失败：(.*)$", r"Chromium failed to start: \1"),
    (r"^浏览器内跳转$", "In-browser navigation"),
    (r"^goto 超时/异常，继续观察页面：(.*)$", r"goto timeout/error, still watching: \1"),
    (r"^页面有 (\d+)s 倒计时，等待中$", r"Countdown \1s, waiting"),
    (r"^点击「(.*)」$", r"Clicked «\1»"),
    (r"^页面已无倒计时/按钮，但仍在广告站内，判定为未解析$",
     "No countdown/button left, but still on the ad site — unresolved"),
    (r"^浏览器耗时 ([\d.]+)s$", r"Browser took \1s"),
)


def to_english(text: str) -> str:
    """把引擎返回的中文提示翻成英文（只处理已知的那几条）。"""
    if not text:
        return text
    out = text
    for pat, rep in _TR:
        if re.search(pat, out):
            out = re.sub(pat, rep, out)
    return out


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

    # ------------------------------------------------------------------ 缓存

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

    # ------------------------------------------------------------ 浏览器兜底

    def _browser(self, url: str) -> Dict[str, Any]:
        from linkresolver.browser import BrowserResolver

        br = BrowserResolver(
            headless=True,
            timeout=self.cfg.browser_timeout,
            proxy=self.cfg.proxy,
        )
        final, steps, note = br.resolve(url)
        return {
            "final": final or url,
            "ok": bool(final) and final != url,
            "steps": [
                {"kind": getattr(s, "kind", "note"), "url": getattr(s, "url", ""),
                 "status": getattr(s, "status", None),
                 "detail": to_english(getattr(s, "detail", ""))}
                for s in (steps or [])
            ],
            "note": to_english(note) or "Browser run finished",
            "method": "browser",
        }

    # ------------------------------------------------------------------ 对外

    async def resolve(self, url: str, allow_browser: Optional[bool] = None) -> Dict[str, Any]:
        allow = self.cfg.allow_browser if allow_browser is None else allow_browser
        key = (url, allow)

        cached = self._cache_get(key)
        if cached is not None:
            out = dict(cached)
            out["cached"] = True
            out["elapsed"] = 0.0
            return out

        # 同一条链接正在解析 -> 直接等它的结果，不重复打站点
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
        t0 = time.perf_counter()
        data = await self.fast.resolve(url)
        data["input"] = url

        # 快路径成功 -> 直接返回（通常 0.2~2s）
        if data.get("ok") and data.get("final"):
            data["elapsed"] = time.perf_counter() - t0
            return data

        # 站点按 IP 拉黑：同一个出口 IP 上跑浏览器也没用，只能换 IP
        if data.get("blocked") == "vpn":
            data["needs_browser"] = False
            data["note"] = "Site blocks datacenter / VPN IPs"
            if self.pool is not None or self.farm is not None:
                got = await self._retry_via_proxy(url, t0)
                if got is not None:
                    return got
            data["elapsed"] = time.perf_counter() - t0
            return data

        heavy = data.get("needs_browser") or sites.needs_browser(url)
        if not allow or not heavy:
            data["elapsed"] = time.perf_counter() - t0
            if not allow and heavy:
                data["note"] = ((data.get("note") + " · ") if data.get("note") else "") + \
                    "Browser fallback is disabled (mode=fast)"
            return data

        # 浏览器兜底
        async with self.bsem:
            b = await asyncio.to_thread(self._browser, data.get("final") or url)

        data["steps"] = (data.get("steps") or []) + (b.get("steps") or [])
        data["method"] = "browser"
        data["elapsed"] = time.perf_counter() - t0
        if b.get("ok"):
            data["ok"] = True
            data["final"] = b["final"]
            data["note"] = b.get("note") or "Resolved with browser"
        else:
            data["ok"] = False
            data["final"] = data.get("final") or url
            data["note"] = b.get("note") or "Browser could not pass the gate"
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
        """站点按 IP 拉黑时，换一个能过墙的出口 IP 再跑一遍。"""
        pool = self.farm or self.pool
        if pool is None:
            return None
        # 农场是空的：后台补货，这次请求不干等
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
        """运行时切换出口代理（同时清缓存，避免复用失败结果）。"""
        self.cfg.proxy = (px or "").strip()
        self.fast = FastResolver(
            timeout=self.cfg.http_timeout,
            max_hops=self.cfg.max_hops,
            proxy=self.cfg.proxy,
        )
        self._proxied.clear()
        self._cache.clear()

    def set_farm(self, on: bool, country: str = "") -> None:
        """开关按国家校验的代理农场；换国家会加载对应池文件。"""
        if country:
            self.cfg.proxy_country = country.strip().upper()[:2]
        self.cfg.proxy_farm = on
        self.farm = Farm(country=self.cfg.proxy_country) if on else None
        self._cache.clear()

    def load(self) -> Tuple[int, int]:
        return self.inflight, self.waiting

    def cache_info(self) -> Tuple[int, int]:
        return len(self._cache), self.hits
