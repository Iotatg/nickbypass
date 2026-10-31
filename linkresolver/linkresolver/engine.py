"""解析引擎：HTTP 快路径（手动跟跳，逐跳记录）+ 浏览器兜底。"""

from __future__ import annotations

import re
import time
import urllib.parse as up
from typing import Dict, List, Optional, Tuple

import httpx

from .models import Result, Step
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
                return True, f"HTTP {status} + 反爬特征「{m}」"
        return True, f"HTTP {status}（被拒绝/限流）"
    low = body[:200000].lower()
    for m in BLOCK_MARKERS:
        if m in low and len(low) < 120000:
            return True, f"反爬/人机验证页「{m}」"
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
        t0 = time.time()
        url = normalize(raw_url)
        res = Result(input_url=raw_url)

        if not url:
            res.note = "空的 URL"
            return res

        # 直接强制浏览器模式
        if self.force_browser and self.allow_browser:
            from .browser import BrowserResolver

            res.steps.append(Step("note", url, detail="强制浏览器模式，跳过 HTTP 快路径"))
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
                    res.steps.append(Step("note", url, detail="检测到循环跳转，停止"))
                    break
                visited.add(url)

                if referer:
                    client.headers.update({"Referer": referer})

                t = time.time()
                try:
                    resp = client.get(url)
                except Exception as exc:
                    res.steps.append(
                        Step("error", url, detail=f"请求失败：{type(exc).__name__}: {exc}", elapsed=time.time() - t)
                    )
                    res.blocked = True
                    res.note = "请求失败（域名不可达 / 超时 / TLS 问题）"
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

                # 1) 重定向
                if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location", "")
                    nxt = up.urljoin(url, loc) if loc else ""
                    if not nxt:
                        res.note = "重定向缺少 Location"
                        break
                    res.steps.append(Step("redirect", nxt, status=resp.status_code, detail=f"HTTP {resp.status_code}"))
                    referer, url = url, nxt
                    continue

                # 2) 反爬 / 人机验证
                blocked, why = is_blocked(resp.status_code, body)
                if blocked:
                    res.steps.append(Step("blocked", url, status=resp.status_code, detail=why))
                    res.blocked = True
                    needs_browser = True
                    res.note = why
                    break

                # 3) 非 HTML：直接当终点（文件/图片/接口）
                if body == "":
                    res.final_url = url
                    res.method = "http"
                    res.ok = True
                    res.note = res.note or f"直接返回 {ctype.split(';')[0] or '二进制'}，非跳转页"
                    break

                # 4) 从页面里找下一跳
                found = R.extract_next(url, body)
                if found:
                    nxt, why, _fields = found
                    nxt = normalize(nxt)
                    if nxt and nxt != url:
                        res.steps.append(Step("extract", nxt, detail=why))
                        referer, url = url, nxt
                        continue

                # 5) 提取不出来：判断是"到终点了"还是"卡在广告门"
                host = R.host_of(url)
                input_host = R.host_of(normalize(raw_url))
                low = body.lower()

                # 广告门特征打分：计时器 + 倒计时文案 + 继续按钮 + 脚本
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
                            detail="页面需要 JS 计时 / 点击才能出终点"
                            + ("（该域名在已知需浏览器名单中）" if R.matches(host, R.NEEDS_BROWSER) else ""),
                        )
                    )
                    res.note = R.HINTS.get(host, "需要浏览器执行 JS 才能拿到终点")
                elif stayed:
                    # 还停在短链/广告站自己的页面上，说明没解析出来（失效链接、错误页、登录墙）
                    res.final_url = url
                    res.method = "http"
                    res.ok = False
                    res.note = "停留在短链站自己的页面上，未解析出终点（链接可能已失效或需要登录）"
                else:
                    res.final_url = url
                    res.method = "http"
                    res.ok = True
                    res.note = res.note or "已到达终点（页面中没有更多跳转）"
                break

            # 6) 浏览器兜底
            if needs_browser and self.allow_browser:
                from .browser import BrowserResolver

                br = BrowserResolver(headless=self.headless, timeout=self.browser_timeout)
                b_url, b_steps, b_note = br.resolve(url)
                res.steps.extend(b_steps)
                res.method = "browser"
                if b_url:
                    res.final_url = b_url
                    res.ok = b_url != normalize(raw_url)
                    res.note = b_note or "浏览器模式解析完成"
                else:
                    res.final_url = url
                    res.ok = False
                    res.note = b_note or "浏览器模式也没能拿到终点（可能需要登录 / 验证码 / 地区限制）"
            elif needs_browser and not self.allow_browser:
                res.note = ((res.note + " · ") if res.note else "") + "该页需要浏览器模式（加 --browser 或在网页里勾选）"
        finally:
            client.close()

        if not res.final_url and res.method == "":
            res.final_url = url
            res.ok = False

        res.elapsed = time.time() - t0
        return res
