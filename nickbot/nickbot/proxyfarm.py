"""Indian proxy farm — harvest, geo-verify, score, persist.

Why this exists
---------------
Several gates (vplink, vplinks, earn4link, ...) refuse datacenter IPs and show a
"VPN Detected" wall. Swapping the exit IP is the only way through, and the IP has
to look *local*: an Indian visitor IP passes, a German one gets walled again.

What this module does
---------------------
1. harvest  : pulls candidates from several public lists that can be filtered by
              country (proxyscrape, proxifly, monosans, TheSpeedX).
2. geo-check: every candidate is asked "what is my IP and country?" through the
              proxy itself (ip-api.com). Only proxies whose *real* exit country
              matches the requested one are kept — list labels lie constantly.
3. site-check: kept proxies are probed against the actual target URL; a proxy
              that opens google but is walled on vplink is useless.
4. persist  : survivors go to data/proxies_<cc>.json with latency + timestamp,
              so a restart does not start from zero.
5. serve    : find_for(url) races the best survivors and returns the first one
              that really loads the target.

Note on "building" proxies: an IP address can only be allocated by the ISP that
owns the block. What can be built is the *pool* — a self-refreshing set of real
Indian exit IPs, plus the rotation logic that keeps a working one in hand.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

# ---------------------------------------------------------------- 数据源

# {cc} = 两位国家码（IN / BD / PK / NP …）
SOURCES: Tuple[Tuple[str, str], ...] = (
    ("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&country={cc}&timeout=10000&ssl=yes&anonymity=all", "plain:http"),
    ("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&country={cc}&timeout=10000&ssl=no", "plain:socks4"),
    ("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&country={cc}&timeout=10000&ssl=no", "plain:socks5"),
    ("https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/{cc}/data.txt", "url"),
    ("https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", "plain:http"),
    ("https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "plain:http"),
    ("https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt", "plain:socks5"),
)

# 免费公共列表里最常见的垃圾端口，先剔掉省时间
JUNK_PORTS = {0, 1, 22, 25, 110, 143, 443, 465, 587, 993, 995, 3306, 3389, 5432, 6379}

PRIVATE_PREFIXES = ("10.", "127.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
                    "172.2", "172.30.", "172.31.", "0.", "169.254.", "224.", "255.")

WALL_MARKERS = (
    "vpn detected", "vpn or proxy", "turn off vpn", "disable vpn", "proxy detected",
    "use a residential", "datacenter ip", "access denied", "not available in your country",
)

# 机房 / 云厂商 ASN 关键词：这类 IP 会被短链站直接 403（实测 DigitalOcean 印度节点也 403）
HOSTING_HINTS = (
    "digitalocean", "amazon", "aws", "google", "microsoft", "azure", "linode", "akamai",
    "vultr", "contabo", "hetzner", "ovh", "oracle", "alibaba", "aliyun", "tencent",
    "hostinger", "colocrossing", "leaseweb", "m247", "datacamp", "cogent", "cloudflare",
    "hosting", "host", "cloud", "server", "data center", "datacenter", "vpn", "proxy",
    "bulletproof", "choopa", "psychz", "quadranet", "hivelocity", "gcore", "scaleway",
    "upcloud", "kamatera", "ionos", "godaddy", "namecheap", "hostwinds", "bandwagon",
)

# 印度本地宽带 / 移动运营商：这类出口才可能通过短链站的住宅 IP 校验
RESIDENTIAL_HINTS = (
    "jio", "reliance", "airtel", "bharti", "vodafone", "idea", "bsnl", "mtnl",
    "act fibernet", "atria", "hathway", "excitel", "tikona", "gtpl", "kerala vision",
    "asianet", "den networks", "siti", "you broadband", "railwire", "railtel",
    "tata teleservices", "tata communications", "tikona", "spectra", "wishnet",
    "broadband", "telecom", "infocom", "communications",
)


def is_residential(isp: str) -> bool:
    """粗判：不是云厂商 ASN，且像本地运营商/宽带。"""
    low = (isp or "").lower()
    if not low:
        return False
    if any(h in low for h in HOSTING_HINTS):
        return False
    return any(r in low for r in RESIDENTIAL_HINTS)

UA = ("Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")

GEO_URLS = (
    "http://ip-api.com/json/?fields=status,countryCode,regionName,city,query",
    "https://ipinfo.io/json",
)

_LINE_RE = re.compile(r"^(?:(?P<scheme>https?|socks[45])://)?(?P<host>\d{1,3}(?:\.\d{1,3}){3}):(?P<port>\d{2,5})$")


# ---------------------------------------------------------------- 工具函数

def norm_proxy(raw: str, default_scheme: str = "http") -> Optional[str]:
    """把 '1.2.3.4:8080' / 'socks5://1.2.3.4:1080' 统一成带 scheme 的 URL。"""
    s = (raw or "").strip().strip(",;\"'")
    if not s:
        return None
    m = _LINE_RE.match(s)
    if not m:
        return None
    host, port = m.group("host"), int(m.group("port"))
    scheme = (m.group("scheme") or default_scheme).lower()
    if port in JUNK_PORTS or not (1 <= port <= 65535):
        return None
    if host.startswith(PRIVATE_PREFIXES):
        return None
    octets = [int(x) for x in host.split(".")]
    if any(o > 255 for o in octets):
        return None
    return f"{scheme}://{host}:{port}"


def parse_lines(text: str, default_scheme: str = "http") -> List[str]:
    out: List[str] = []
    for line in (text or "").splitlines():
        p = norm_proxy(line, default_scheme)
        if p:
            out.append(p)
    return out


def _geo_country(cc: str) -> str:
    return (cc or "IN").strip().upper()[:2]


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


async def _bounded(coro, timeout: float):
    """硬超时。SOCKS 握手的读等待有时不受 httpx timeout 约束，必须在外层再套一层。"""
    try:
        return await asyncio.wait_for(coro, timeout=max(0.5, timeout))
    except Exception:
        return None


class Farm:
    """按国家维护的可用出口 IP 池（默认印度）。"""

    def __init__(
        self,
        country: str = "IN",
        path: Optional[Path] = None,
        ttl: float = 1800.0,
        max_keep: int = 150,
    ) -> None:
        self.country = _geo_country(country)
        self.ttl = ttl
        self.max_keep = max_keep
        base = Path(__file__).resolve().parent.parent / "data"
        self.path = Path(path) if path else base / f"proxies_{self.country.lower()}.json"
        self._db: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._last_refresh = 0.0
        self._refreshing = False
        self._by_host: Dict[str, Tuple[float, str]] = {}   # host -> (ts, proxy)
        self.last_report: Dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------ 持久化

    def _load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._db = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            self._db = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._db, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    # ------------------------------------------------------------ 抓取

    async def harvest(self, limit: int = 220) -> List[str]:
        found: List[str] = []

        async def one(client: httpx.AsyncClient, tpl: str, kind: str) -> None:
            url = tpl.format(cc=self.country)
            scheme = kind.split(":", 1)[1] if ":" in kind else "http"
            try:
                r = await client.get(url, timeout=12)
                if r.status_code != 200:
                    return
                txt = r.text
                if kind == "url":
                    found.extend(parse_lines(txt, "http"))
                else:
                    found.extend(parse_lines(txt, scheme))
            except Exception:
                return

        async with httpx.AsyncClient(follow_redirects=True, verify=False) as c:
            await asyncio.gather(*(one(c, tpl, kind) for tpl, kind in SOURCES))

        # 去重；已知可用的排前面
        known = [p for p in self._db if self._db[p].get("ok")]
        rest = [p for p in dict.fromkeys(found) if p not in self._db]
        random.shuffle(rest)
        return (known + rest)[:limit]

    # ------------------------------------------------------------ 校验

    async def _exit_ip(self, proxy: str, timeout: float) -> Optional[str]:
        """通过代理问「我的出口 IP 是多少」。只取 IP，国家稍后批量查，省掉每代理一次查询。"""
        for url in GEO_URLS:
            try:
                async with httpx.AsyncClient(proxy=proxy, verify=False,
                                             timeout=httpx.Timeout(timeout, connect=timeout),
                                             follow_redirects=False) as c:
                    r = await c.get(url)
                    if r.status_code != 200:
                        continue
                    txt = (r.text or "").strip()
                    if txt.startswith("{"):
                        data = r.json()
                        ip = data.get("query") or data.get("ip") or ""
                    else:
                        ip = txt.split()[0] if txt else ""
                    ip = ip.strip()
                    if ip.count(".") == 3:
                        return ip
            except Exception:
                continue
        return None

    async def _batch_geo(self, ips: List[str]) -> Dict[str, Dict[str, Any]]:
        """本地批量查国家（ip-api batch，100 个/次，不经过代理，快且准）。"""
        out: Dict[str, Dict[str, Any]] = {}
        uniq = list(dict.fromkeys([i for i in ips if i]))
        async with httpx.AsyncClient(timeout=12) as c:
            for i in range(0, len(uniq), 100):
                chunk = uniq[i:i + 100]
                try:
                    r = await c.post(
                        "http://ip-api.com/batch?fields=status,countryCode,city,query,as,isp",
                        json=[{"query": ip} for ip in chunk],
                    )
                    if r.status_code != 200:
                        continue
                    for row in r.json():
                        if row.get("status") == "success":
                            out[row.get("query", "")] = {
                                "cc": (row.get("countryCode") or "").upper()[:2],
                                "city": row.get("city") or "",
                                "as": row.get("as") or "",
                                "isp": row.get("isp") or row.get("as") or "",
                            }
                except Exception:
                    continue
        return out

    async def _probe_site(self, proxy: str, url: str, timeout: float) -> bool:
        try:
            async with httpx.AsyncClient(proxy=proxy, verify=False, follow_redirects=False,
                                         timeout=httpx.Timeout(timeout, connect=timeout),
                                         headers={"User-Agent": UA,
                                                  "Accept-Language": "en-IN,en;q=0.9"}) as c:
                r = await c.get(url)
                if r.status_code >= 500:
                    return False
                body = (r.text or "").lower()
                if any(m in body for m in WALL_MARKERS):
                    return False
                return r.status_code < 400 or bool(r.headers.get("location"))
        except Exception:
            return False

    # ------------------------------------------------------------ 刷新

    async def refresh(
        self,
        target: Optional[str] = None,
        limit: int = 220,
        budget: float = 40.0,
        workers: int = 45,
        geo_only: bool = False,
    ) -> Dict[str, Any]:
        """抓取 → 出口 IP → 国家校验 →（可选）站点校验，结果写回磁盘。"""
        if self._refreshing:
            return {"skipped": "already running"}
        self._refreshing = True
        t0 = time.perf_counter()
        try:
            # limit=0 -> 不抓新货，只对现有池做站点复检
            cands = await self.harvest(limit=limit) if limit > 0 else self._ranked()
            sem = asyncio.Semaphore(workers)
            deadline = time.perf_counter() + budget
            ips: Dict[str, str] = {}          # proxy -> exit ip
            lat: Dict[str, int] = {}

            async def grab(px: str) -> None:
                async with sem:
                    left = deadline - time.perf_counter()
                    if left < 1.5:
                        return
                    t = time.perf_counter()
                    ip = await _bounded(self._exit_ip(px, min(6.0, left)), min(7.0, left))
                    if ip:
                        ips[px] = ip
                        lat[px] = int((time.perf_counter() - t) * 1000)

            # 阶段一：并发问出口 IP（这一步就能刷掉 80% 死代理）
            verified: List[str] = []
            if limit > 0:
                await _bounded(asyncio.gather(*(grab(p) for p in cands)), budget + 5)

                # 阶段二：本地批量查国家
                geo = await self._batch_geo(list(ips.values())) if ips else {}
                for px, ip in ips.items():
                    g = geo.get(ip) or {}
                    if g.get("cc") != self.country:
                        self._db.pop(px, None)
                        continue
                    isp = g.get("isp") or g.get("as") or ""
                    self._db[px] = {"ok": True, "cc": g["cc"], "ip": ip,
                                    "city": g.get("city", ""), "isp": isp[:40],
                                    "residential": is_residential(isp),
                                    "latency": lat.get(px, 0),
                                    "checked": int(time.time())}
                    verified.append(px)
            else:
                # 只复检现有池，不重新抓、不重新查国家
                verified = self._ranked()

            # 阶段三：站点校验（只对真·本地 IP 做）
            site_ok = 0
            if target and not geo_only and verified:
                site_deadline = time.perf_counter() + max(6.0, budget - (time.perf_counter() - t0))
                ssem = asyncio.Semaphore(max(8, workers // 2))

                async def probe(px: str) -> None:
                    nonlocal site_ok
                    async with ssem:
                        left = site_deadline - time.perf_counter()
                        if left < 2.0:
                            return
                        ok = await _bounded(self._probe_site(px, target, min(7.0, left)),
                                            min(8.0, left))
                        self._db.setdefault(px, {})["site"] = bool(ok)
                        if ok:
                            site_ok += 1

                await _bounded(asyncio.gather(*(probe(p) for p in verified)), 40)

            # 清理：过期 + 超量
            now = time.time()
            for p in [p for p, v in self._db.items()
                      if now - float(v.get("checked", 0)) > self.ttl * 4]:
                self._db.pop(p, None)
            if len(self._db) > self.max_keep:
                keep = sorted(self._db.items(),
                              key=lambda kv: (not kv[1].get("site", False),
                                              kv[1].get("latency", 9999)))[: self.max_keep]
                self._db = dict(keep)

            self._last_refresh = now
            self._save()
            self.last_report = {
                "country": self.country,
                "candidates": len(cands),
                "reached": len(ips),
                "verified": len(verified),
                "site_ok": site_ok,
                "kept": len(self._db),
                "seconds": round(time.perf_counter() - t0, 1),
                "target": target or "",
            }
            return dict(self.last_report)
        finally:
            self._refreshing = False

    # ------------------------------------------------------------ 对外

    def _ranked(self, exclude: Optional[Iterable[str]] = None) -> List[str]:
        skip = set(exclude or ())
        items = [(p, v) for p, v in self._db.items()
                 if p not in skip and v.get("ok") and v.get("cc") == self.country]
        items.sort(key=lambda kv: (not kv[1].get("residential", False),
                                   not kv[1].get("site", False),
                                   kv[1].get("latency", 9999)))
        return [p for p, _ in items]

    async def find_for(self, url: str, budget: float = 8.0,
                       exclude: Optional[Iterable[str]] = None,
                       workers: int = 12,
                       allow_refresh: bool = False) -> Optional[str]:
        """返回第一个真能打开 url 的本地出口代理。池空时默认不刷新（不拖慢请求）。"""
        host = _host_of(url)
        hit = self._by_host.get(host)
        if hit and time.time() - hit[0] < 300 and hit[1] not in set(exclude or ()):
            return hit[1]

        cands = self._ranked(exclude)
        if not cands and allow_refresh:
            await self.refresh(target=url, limit=120, budget=min(25.0, budget + 12))
            cands = self._ranked(exclude)
        if not cands:
            return None

        deadline = time.perf_counter() + max(2.0, budget)
        sem = asyncio.Semaphore(workers)

        async def one(px: str) -> Optional[str]:
            async with sem:
                left = deadline - time.perf_counter()
                if left < 1.0:
                    return None
                ok = await _bounded(self._probe_site(px, url, min(6.0, left)),
                                    min(7.0, left))
                if ok:
                    self._db.setdefault(px, {})["site"] = True
                return px if ok else None

        tasks = [asyncio.create_task(one(p)) for p in cands[:40]]
        winner: Optional[str] = None
        try:
            pending = set(tasks)
            while pending and winner is None:
                left = deadline - time.perf_counter()
                if left <= 0:
                    break
                done, pending = await asyncio.wait(pending, timeout=left,
                                                   return_when=asyncio.FIRST_COMPLETED)
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
            self._by_host[host] = (time.time(), winner)
            self._save()
        return winner

    def add(self, raw: str) -> Optional[str]:
        px = norm_proxy(raw)
        if not px:
            return None
        self._db[px] = {"ok": True, "cc": self.country, "checked": int(time.time()),
                        "latency": 0, "city": "manual", "ip": ""}
        self._save()
        return px

    def remove(self, raw: str) -> bool:
        px = norm_proxy(raw) or raw.strip()
        if px in self._db:
            self._db.pop(px, None)
            self._save()
            return True
        return False

    def drop(self, proxy: str) -> None:
        """探测失败时调用：保留记录但降级，连续失败就丢掉。"""
        self._by_host = {h: v for h, v in self._by_host.items() if v[1] != proxy}
        rec = self._db.get(proxy)
        if not rec:
            return
        fails = int(rec.get("fails", 0)) + 1
        rec["fails"] = fails
        rec["site"] = False
        if fails >= 2:
            self._db.pop(proxy, None)
        self._save()

    def clear(self) -> int:
        n = len(self._db)
        self._db = {}
        self._by_host = {}
        self._save()
        return n

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        ok = [v for v in self._db.values() if v.get("ok")]
        site = [v for v in ok if v.get("site")]
        fresh = [v for v in ok if now - float(v.get("checked", 0)) < self.ttl]
        lat = sorted(int(v.get("latency", 0) or 0) for v in ok if v.get("latency"))
        return {
            "country": self.country,
            "total": len(self._db),
            "fresh": len(fresh),
            "site_ok": len(site),
            "residential": sum(1 for v in ok if v.get("residential")),
            "median_ms": lat[len(lat) // 2] if lat else 0,
            "last_refresh": int(self._last_refresh),
            "path": str(self.path),
        }

    def sample(self, n: int = 5) -> List[str]:
        return self._ranked()[:n]


# ---------------------------------------------------------------- CLI

def _main() -> None:  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="Build a country proxy pool")
    ap.add_argument("--country", default="IN")
    ap.add_argument("--target", default="", help="also probe this URL")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--budget", type=float, default=45)
    ap.add_argument("--show", type=int, default=8)
    a = ap.parse_args()

    async def run() -> None:
        f = Farm(country=a.country)
        rep = await f.refresh(target=a.target or None, limit=a.limit, budget=a.budget)
        print(json.dumps(rep, indent=2))
        for p in f.sample(a.show):
            print(" ", p, f._db.get(p, {}).get("latency", "?"))

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    _main()
