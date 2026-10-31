"""浏览器模式：用 Chromium 真跑一遍 JS，点掉倒计时和"继续"按钮，抓最终地址。

这是现在唯一对 Linkvertise / GPLinks / ouo 这类"广告门"有效的方式：
它们的终点是 JS 动态生成的，纯 HTTP 请求永远看不到。
"""

from __future__ import annotations

import os
import re
import time
from typing import List, Optional, Tuple

from .models import Step
from .rules import host_of

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 广告/统计/弹窗域名，直接拦掉，省时间也更接近"人"的行为
BLOCK_HOSTS = (
    "doubleclick.net", "googlesyndication.com", "google-analytics.com", "googletagmanager.com",
    "adservice.google.com", "popads.net", "popcash.net", "propellerads.com", "onclickads.net",
    "adsterra.com", "exoclick.com", "juicyads.com", "trafficjunky.net", "hilltopads.net",
    "mgid.com", "taboola.com", "outbrain.com", "zedo.com", "adcash.com", "clickadu.com",
    "facebook.net", "hotjar.com", "yandex.ru/metrika", "mc.yandex.ru", "cloudflareinsights.com",
)

CLICK_RX = (
    r"(continue|get\s*link|getlink|proceed|skip\s*ad|skip|click\s*here|download|verify|"
    r"i'?m\s*not\s*a\s*robot|unlock|go\s*to\s*link|open\s*link|free\s*access|获取|继续|跳过)"
)

JS_FIND = r"""
(rx) => {
  const r = new RegExp(rx, 'i');
  const sel = 'a,button,[role=button],input[type=submit],div[onclick],span[onclick],div.btn,span.btn';
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    const b = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    if (b.width < 8 || b.height < 8) continue;
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity || '1') < 0.3) continue;
    if (b.top > window.innerHeight * 3) continue;
    let t = (e.innerText || e.value || '').trim().replace(/\s+/g, ' ');
    if (!t) continue;
    let score = 0;
    if (r.test(t)) score += 10;
    if (e.tagName === 'BUTTON' || e.tagName === 'A') score += 2;
    if (e.id && /(btn|continue|get|link|go|submit)/i.test(e.id)) score += 3;
    if (e.className && /(btn|button|continue|getlink|submit)/i.test(String(e.className))) score += 2;
    if (/(ad|banner|popup|promo|sponsor)/i.test(t)) score -= 9;
    if (/^(×|x|✕|close|关闭)$/i.test(t)) score -= 12;
    if (score >= 10) out.push({t: t.slice(0, 60), score: score});
  }
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, 5).map(o => o.t);
}
"""

JS_CLICK = r"""
(label) => {
  const sel = 'a,button,[role=button],input[type=submit],div[onclick],span[onclick],div.btn,span.btn';
  for (const e of document.querySelectorAll(sel)) {
    const t = (e.innerText || e.value || '').trim().replace(/\s+/g, ' ');
    if (t !== label) continue;
    const b = e.getBoundingClientRect();
    if (b.width < 8 || b.height < 8) continue;
    e.scrollIntoView({block: 'center'});
    e.click();
    return true;
  }
  return false;
}
"""


def _gate_hosts(start_host: str) -> Tuple[str, ...]:
    return (start_host, "linkvertise.com", "link-to.net", "ouo.io", "ouo.press", "gplinks.co",
            "gplinks.in", "shrinkme.io", "shrinke.me", "adfoc.us", "boost.ink", "exe.io",
            "aylink.co", "tnlink.in", "mdisk.pro", "cuty.io", "tii.ai", "clk.sh", "za.gl")


class BrowserResolver:
    def __init__(self, headless: bool = True, timeout: float = 30.0, proxy: str = "") -> None:
        self.headless = headless
        self.timeout = timeout
        self.proxy = (proxy or "").strip()

    def _launch(self, p):
        """优先用 Playwright 自带 Chromium，退回系统 Chromium。"""
        candidates = [
            dict(headless=self.headless, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]),
        ]
        try:
            return p.chromium.launch(**candidates[0])
        except Exception:
            pass
        exe = os.environ.get("CHROME_PATH", "")
        if not exe:
            for cand in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
                if os.path.exists(cand):
                    exe = cand
                    break
        if exe:
            return p.chromium.launch(executable_path=exe, **candidates[0])
        return p.chromium.launch(**candidates[0])

    def resolve(self, url: str) -> Tuple[str, List[Step], str]:
        steps: List[Step] = []
        start_host = host_of(url)
        gates = _gate_hosts(start_host)
        final = ""
        note = ""

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return "", [Step("error", url, detail=f"Playwright 不可用：{exc}")], "未安装 playwright"

        with sync_playwright() as p:
            try:
                browser = self._launch(p)
            except Exception as exc:
                return "", [Step("error", url, detail=f"Chromium 启动失败：{exc}")], "浏览器启动失败"

            ctx_kwargs = dict(
                user_agent=UA,
                locale="en-US",
                viewport={"width": 1366, "height": 800},
                ignore_https_errors=True,
            )
            if self.proxy:
                ctx_kwargs["proxy"] = {"server": self.proxy}
            ctx = browser.new_context(**ctx_kwargs)
            ctx.add_init_script("window.open = function(){ return {focus(){}, close(){}, closed:true}; };")
            page = ctx.new_page()
            page.set_default_timeout(15000)

            # 拦广告 / 统计
            def _route(route):
                try:
                    h = host_of(route.request.url)
                    if any(h == b or h.endswith("." + b) or b in h for b in BLOCK_HOSTS):
                        return route.abort()
                except Exception:
                    pass
                return route.continue_()

            ctx.route("**/*", _route)

            # 记录主框架跳转
            nav = {"last": ""}

            def _on_nav(frame):
                if frame != page.main_frame:
                    return
                u = frame.url
                if u and u != nav["last"]:
                    steps.append(Step("redirect", u, detail="浏览器内跳转"))
                    nav["last"] = u

            page.on("framenavigated", _on_nav)

            t0 = time.time()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000 * 0.6))
            except Exception as exc:
                steps.append(Step("note", url, detail=f"goto 超时/异常，继续观察页面：{type(exc).__name__}"))

            deadline = time.time() + self.timeout
            clicked: set[str] = set()
            waited_countdown = False
            idle = 0

            while time.time() < deadline:
                cur = page.url
                cur_host = host_of(cur)

                # 已经离开广告门域名 -> 认为到终点
                if cur and cur != url and cur_host and not any(
                    cur_host == g or cur_host.endswith("." + g) for g in gates
                ):
                    final = cur
                    break

                body = ""
                try:
                    body = page.inner_text("body")[:4000]
                except Exception:
                    pass

                # 倒计时：等它走完
                m = re.search(r"(\d{1,2})\s*(?:s|sec|secs|second|seconds)\b", body, re.I)
                if m and not waited_countdown:
                    waited_countdown = True
                    steps.append(Step("note", cur, detail=f"页面有 {m.group(1)}s 倒计时，等待中"))

                # 找按钮并点
                try:
                    labels = page.evaluate(JS_FIND, CLICK_RX)
                except Exception:
                    labels = []

                acted = False
                for label in labels:
                    if label in clicked:
                        continue
                    clicked.add(label)
                    try:
                        ok = page.evaluate(JS_CLICK, label)
                    except Exception:
                        ok = False
                    if ok:
                        steps.append(Step("click", cur, detail=f"点击「{label}」"))
                        acted = True
                        page.wait_for_timeout(1200)
                        break

                if not acted:
                    idle += 1
                    # 换了页面 + 没有可点的东西 + 没有倒计时：如果已经离开广告站才是终点，
                    # 还停在广告站（比如跳到 /explore、/404）就是没解析出来
                    if page.url != url and idle >= 2 and not labels and not m:
                        steps.append(
                            Step("note", page.url, detail="页面已无倒计时/按钮，但仍在广告站内，判定为未解析")
                        )
                        break
                    page.wait_for_timeout(900)
                else:
                    idle = 0

            if not final:
                note = "浏览器跑完仍未离开广告门（可能需要验证码 / 登录 / 地区限制，或该链接本身失效）"
            else:
                note = "浏览器模式拿到终点"

            steps.append(Step("note", final or page.url, detail=f"浏览器耗时 {time.time() - t0:.1f}s"))
            ctx.close()
            browser.close()

        return final, steps, note
