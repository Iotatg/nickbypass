"""Fast async resolver.

Why this exists: the old path opened a brand-new sync HTTP client for every
link (fresh TLS handshake, no keep-alive) and then fell back to a full
Chromium boot. That is why a 4-hop shortlink took 30s.

This module does three things:

1. One shared ``httpx.AsyncClient`` with HTTP/2 + keep-alive, so hop #2..N
   cost ~1ms instead of a new handshake.
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx

# 解析引擎（linkresolver）默认放在项目同级目录，允许用环境变量覆盖
_HOME = Path(
    os.getenv("LINKRESOLVER_HOME")
    or (Path(__file__).resolve().parent.parent.parent / "linkresolver")
)
if str(_HOME) not in sys.path:
    sys.path.insert(0, str(_HOME))

try:
    from linkresolver import normalize  # noqa: E402
    from linkresolver.rules import extract_next, host_of  # noqa: E402
except ModuleNotFoundError as _exc:  # pragma: no cover - deploy-time hint
    raise ModuleNotFoundError(
        f"resolver engine not importable from {_HOME}. Clone the whole repo "
        "(nickbot/ + linkresolver/ side by side) or set LINKRESOLVER_HOME."
    ) from _exc

# --------------------------------------------------------------------------- 常量

UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 站点明确拒绝机房 / VPN IP 时的文案特征
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

# Cloudflare / 人机验证
CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "enable javascript and cookies to continue",
    "attention required",
    "error code: 1020",
    "error code: 1000",
)

# 同属一个建站模板（"GT Links"）的站点：都有 POST /links/go。
# 名单本身在 registry.py 里（kind="gt"），这里只留一个兜底，防止 registry 缺失。
GT_FAMILY_FALLBACK = (
    "vplink", "gplinks", "arolinks", "liteshort", "inshorturl",
    "vipshort", "cpmshort", "earn4link", "earnlink", "shrinkme",
    "tii.la", "tpi.li", "oii.la", "clk.sh", "za.gl",
)

# 纯静态资源：不要为它们浪费一跳
ASSET_EXT = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".mp4", ".mp3",
)


def _is_gt(host: str) -> bool:
    """是不是那个同模板的 PHP 短链系统（有 POST /links/go）。"""
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
    """广告门特征打分：倒计时 + 计时器 + 继续按钮 + 脚本。"""
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


def _challenge(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in CHALLENGE_MARKERS) and len(low) < 200_000


# 短链站自己承认链接有问题时用的文案。只在"这个域名是短链站"时才查，
# 否则任何内容页都可能误伤。
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
    """短链站返回"链接无效 / 已过期"页 —— 这不是解析成功。"""
    if _site_of(url) is None:
        return False
    low = html.lower()
    return any(m in low for m in DEAD_MARKERS)


# --------------------------------------------------------------------------- 引擎


# 广告链中间站：这些域名是"过路站"，不是终点。打不开时不能当成解析成功，
# 否则用户会拿到一条 pipefitter.in 之类的广告页当结果。
AD_HOSTS = {
    "pipefitter.in",
    "adf.ly", "adfoc.us", "shorte.st", "shrinkme.io", "shrinkearn.com",
    "ouo.io", "ouo.press", "linkvertise.com", "linkvertise.net",
    "gplinks.co", "gplink.in", "vplink.in", "vplinks.in", "arolinks.com",
    "liteshort.co", "inshorturl.com", "earn4link.in", "linkpays.in",
    "mdiskshortner.link", "clicksfly.com", "dropgalaxy.com",
}


def _is_terminal(cur: str, start_host: str) -> bool:
    """cur 看起来是终点（而不是广告链中间站）吗？"""
    h = host_of(cur)
    if not h or h == start_host or h in AD_HOSTS:
        return False
    try:
        from . import sites as _sites

        # 传完整 URL：生成名单里的域名只有路径像短码时才算广告站
        if _sites.is_gate(cur):
            return False
    except Exception:
        pass
    return True


class FastResolver:
    """共享连接池的异步解析器。"""

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

    async def client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._lock:
            if self._client is None or self._client.is_closed:
                # 走代理时握手更慢，给连接阶段单独放宽
                connect = 10.0 if self.proxy else 6.0
                kwargs: Dict[str, Any] = dict(
                    timeout=httpx.Timeout(self.timeout, connect=connect),
                    follow_redirects=False,
                    verify=False,
                    # 便宜 HTTP 代理大多不支持 h2 CONNECT，走代理时退回 HTTP/1.1
                    http2=not bool(self.proxy),
                    limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
                    headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                if self.proxy:
                    kwargs["proxy"] = self.proxy
                self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ 主流程

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

        steps: List[Dict[str, Any]] = out["steps"]
        start_host = host_of(url)
        cur = url
        visited: set[str] = set()
        # 已经离开广告门、且正常返回 200 的地址。广告链最后常把页面弹回
        # pipefitter 之类的中间站，这时要退回这里，而不是把广告页当结果。
        best_dest = ""
        # 只有当当前 URL 是站点自己 30x 声明的跳转时，"打不开"才说明终点挂了；
        # 从广告页 HTML 里抠出来的下一跳多半还是广告链中间站。
        via_redirect = False

        try:
            client = await self.client()
            for _ in range(self.max_hops):
                if cur in visited:
                    steps.append({"kind": "note", "url": cur, "detail": "Redirect loop detected"})
                    break
                visited.add(cur)

                try:
                    resp = await client.get(cur, headers={"User-Agent": UA_MOBILE, "Referer": url if cur != url else ""})
                except httpx.HTTPError as exc:
                    steps.append({"kind": "error", "url": cur, "detail": f"{type(exc).__name__}: {exc}"})
                    if _is_terminal(cur, start_host):
                        # 已经拿到终点地址，只是终点连不上 —— 解析本身是成功的
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
                    # 终点页把用户又弹回广告层：停在这里，用上一跳当结果
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
                    # 403/451 + 极短页面 = 按 IP 直接拒绝（机房段），换出口 IP 才有用
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
                        # 已经到终点了，只是终点本身挂了 —— 这算解析成功
                        out["final"] = cur
                        out["ok"] = True
                        out["note"] = f"Destination returned HTTP {resp.status_code} (link may be dead)"
                    else:
                        out["note"] = f"Site returned HTTP {resp.status_code}"
                        out["final"] = cur
                    break

                html = resp.text or ""

                # 0) 短链站自己说"这链接无效 / 过期了" —— 别把警告页当终点
                if _dead_link(html, cur):
                    steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                  "detail": "Shortener reports an invalid / expired link"})
                    out["final"] = cur
                    out["note"] = ("The shortener itself says this link is invalid or expired — "
                                   "nothing to unpack.")
                    break

                # 记下"已经离开广告门、且正常返回"的最深一跳
                if _is_terminal(cur, start_host):
                    best_dest = cur

                # 1) VPN / 机房 IP 拦截
                if _vpn_wall(html):
                    steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                  "detail": "Site shows 'VPN detected' wall"})
                    out["blocked"] = "vpn"
                    out["note"] = (
                        "This site blocks datacenter / VPN IPs. It refuses the server's IP, "
                        "so no amount of retries will work from here — a residential proxy is required."
                    )
                    break

                # 2) Cloudflare 挑战
                if _challenge(html):
                    steps.append({"kind": "blocked", "url": cur, "status": resp.status_code,
                                  "detail": "Anti-bot / Cloudflare challenge"})
                    out["needs_browser"] = True
                    out["note"] = "Anti-bot challenge — needs a real browser"
                    break

                # 3) 站点 API（GT Links 家族）：一次 POST 直接拿终点
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

                # 4) 通用提取：meta refresh / JS location / base64 / 表单 / 按钮
                nxt = extract_next(cur, html)
                if nxt:
                    target, detail, form = nxt
                    if target and target != cur:
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

                # 5) 没有下一跳：是终点，还是"卡在广告门上"？
                still_gate = False
                try:
                    from . import sites as _sites

                    still_gate = _sites.is_gate(cur)
                except Exception:
                    pass

                if host_of(cur) == start_host and (_gate_score(html) >= 3 or still_gate):
                    steps.append({"kind": "note", "url": cur,
                                  "detail": "No destination in the HTML — still on the shortener"})
                    # gate / gt 值得让浏览器再试一次；纯跳转站就算了，白等
                    heavy = True
                    try:
                        from . import sites as _sites

                        heavy = _sites.needs_browser(cur)
                    except Exception:
                        pass
                    out["needs_browser"] = heavy
                    out["note"] = ("Still on the shortener page — the destination is built by "
                                   "JavaScript, not in the HTML")
                    break

                out["final"] = cur
                out["ok"] = True
                out["note"] = "Reached destination (no further redirect)"
                break

            # 兜底：最后停在广告站，但之前已经到过真终点
            if host_of(out.get("final", "")) in AD_HOSTS and best_dest:
                out["final"] = best_dest
                out["ok"] = True
                out["note"] = "Reached destination (ad layer bounced)"

            if not out["final"] and out["method"]:
                out["final"] = cur

        except Exception as exc:  # 引擎异常不该让 bot 崩
            out["note"] = f"Resolver error: {type(exc).__name__}: {exc}"
            steps.append({"kind": "error", "url": cur, "detail": str(exc)[:200]})

        out["elapsed"] = time.perf_counter() - t0
        return out

    # ------------------------------------------------------------ 站点 API 适配

    async def _gate_json(
        self, client: httpx.AsyncClient, page_url: str, html: str
    ) -> Tuple[str, str, str]:
        """POST {origin}/links/go → {"status":"success","url":"https://..."}

        很多同模板站点只认 ``link=<slug>``；也有站点要求 CSRF token。
        这里按"最可能成功"的顺序试，任何一个返回可用 URL 就停。
        """
        origin = _origin(page_url)
        slug = _slug_of(page_url)
        if not slug:
            return "", "", ""

        tokens = re.findall(r'name=["\'](_?token|_csrf)["\'][^>]*value=["\']([^"\']+)', html, re.I)
        base_headers = {
            "User-Agent": UA_MOBILE,
            "Referer": page_url,
            "Origin": origin,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

        payloads: List[Dict[str, str]] = [{"link": slug}]
        if tokens:
            payloads.insert(0, {"link": slug, tokens[0][0]: tokens[0][1]})
        payloads.append({"link": slug, "ref": ""})

        for data in payloads:
            for path in ("/links/go", "/links/go/"):
                try:
                    r = await client.post(origin + path, data=data, headers=base_headers)
                except httpx.HTTPError:
                    continue
                try:
                    j = r.json()
                except Exception:
                    continue
                if not isinstance(j, dict):
                    continue
                target = (j.get("url") or j.get("link") or "").strip()
                msg = str(j.get("message") or "")
                if target.startswith("http") and host_of(target) != host_of(page_url):
                    return target, msg, ""
                # status=error + message：把站点的原话带回去，别自己编
                if str(j.get("status")).lower() == "error" and msg:
                    return "", "", msg
        return "", "", ""
