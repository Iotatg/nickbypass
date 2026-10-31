"""Proxy pool (opt-in).

Some gates (vplink, vplinks, ...) refuse datacenter IPs outright. The only way
through is a different exit IP, so this module keeps a pool of candidate
proxies and hands out one that actually works **for the site being resolved**.

Design notes
* Never blocks startup — the pool is filled lazily on first use.
* Proxies are validated against the *target URL*, not a generic 204 probe:
  a proxy that answers gstatic can still be walled on vplink.
* Candidates are raced in parallel; the first acceptable one wins (~2-5s).
* Working proxies are cached per host for 10 minutes.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

import httpx

SOURCES = (
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&country=IN&timeout=5000&ssl=yes",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&country=BD&timeout=5000&ssl=yes",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
)

UA = (
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)

WALL_MARKERS = (
    "vpn detected",
    "turn off vpn",
    "disable vpn",
    "vpn or proxy",
    "proxy detected",
)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


class ProxyPool:
    def __init__(self, ttl: float = 600.0, max_candidates: int = 80) -> None:
        self.ttl = ttl
        self.max_candidates = max_candidates
        self._cands: List[str] = []
        self._cands_at = 0.0
        self._by_host: Dict[str, Tuple[float, List[str]]] = {}
        self._dead: Set[str] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 抓取

    async def _fetch(self, client: httpx.AsyncClient) -> List[str]:
        out: List[str] = []
        for src in SOURCES:
            try:
                r = await client.get(src, timeout=8)
                if r.status_code != 200:
                    continue
                for line in r.text.splitlines():
                    line = line.strip()
                    if not line or ":" not in line or " " in line:
                        continue
                    host, _, port = line.rpartition(":")
                    if port.isdigit() and host.count(".") == 3:
                        out.append(f"http://{host}:{port}")
            except Exception:
                continue
        uniq = [p for p in dict.fromkeys(out) if p not in self._dead]
        random.shuffle(uniq)
        return uniq[: self.max_candidates]

    async def candidates(self, force: bool = False) -> List[str]:
        if not force and self._cands and time.time() - self._cands_at < 300:
            return list(self._cands)
        async with self._lock:
            if not force and self._cands and time.time() - self._cands_at < 300:
                return list(self._cands)
            try:
                async with httpx.AsyncClient(timeout=10, verify=False) as c:
                    self._cands = await self._fetch(c)
            except Exception:
                self._cands = []
            self._cands_at = time.time()
            return list(self._cands)

    # ------------------------------------------------------------------ 探测

    async def _probe_site(self, proxy: str, url: str, timeout: float) -> bool:
        try:
            async with httpx.AsyncClient(
                proxy=proxy,
                verify=False,
                follow_redirects=False,
                timeout=httpx.Timeout(timeout, connect=timeout),
                headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
            ) as c:
                r = await c.get(url)
                if r.status_code >= 500:
                    return False
                body = (r.text or "").lower()
                if any(m in body for m in WALL_MARKERS):
                    return False
                # 200/30x 都算过墙（30x 说明站点开始跳转了）
                return r.status_code < 400 or bool(r.headers.get("location"))
        except Exception:
            return False

    # ------------------------------------------------------------------ 对外

    async def find_for(
        self,
        url: str,
        budget: float = 6.0,
        exclude: Optional[Iterable[str]] = None,
        workers: int = 24,
    ) -> Optional[str]:
        """返回一个能打开 url 且不是 VPN 墙的代理；找不到返回 None。"""
        host = _host(url)
        skip = set(exclude or ()) | self._dead

        cached = self._by_host.get(host)
        if cached and time.time() - cached[0] < self.ttl:
            for p in cached[1]:
                if p not in skip:
                    return p

        cands = [p for p in await self.candidates() if p not in skip]
        if not cands:
            return None

        deadline = time.perf_counter() + max(1.0, budget)
        sem = asyncio.Semaphore(workers)

        async def one(p: str) -> Optional[str]:
            async with sem:
                left = deadline - time.perf_counter()
                if left <= 0.4:
                    return None
                ok = await self._probe_site(p, url, min(5.0, left))
                return p if ok else None

        tasks = [asyncio.create_task(one(p)) for p in cands]
        winner: Optional[str] = None
        try:
            pending = set(tasks)
            while pending and winner is None:
                left = deadline - time.perf_counter()
                if left <= 0:
                    break
                done, pending = await asyncio.wait(
                    pending, timeout=left, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    break
                for t in done:
                    res = t.result()
                    if res:
                        winner = res
                        break
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if winner:
            self._by_host[host] = (time.time(), [winner])
        return winner

    async def get(self, n: int = 2) -> List[str]:
        """兼容旧接口：不做站点探测，只给候选。"""
        return (await self.candidates())[:n]

    async def drop(self, proxy: str) -> None:
        self._dead.add(proxy)
        for host, (ts, lst) in list(self._by_host.items()):
            keep = [p for p in lst if p != proxy]
            if keep:
                self._by_host[host] = (ts, keep)
            else:
                self._by_host.pop(host, None)

    def info(self) -> Dict[str, object]:
        alive = sum(len(v[1]) for v in self._by_host.values())
        best = ""
        for _, lst in self._by_host.values():
            if lst:
                best = lst[0]
                break
        return {
            "candidates": len(self._cands),
            "alive": alive,
            "dead": len(self._dead),
            "best": best,
        }
