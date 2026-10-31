#!/usr/bin/env python3
"""Watch a gate link in a real browser and print every navigation it makes.

The phone and the repo disagree about what this file is: the repo carries a
Playwright engine, the phone runs a Selenium one that is not in git. So the
first thing this tool does is report which implementation is actually loaded
(path + sha256 + engine), then trace *that* one - it never converts, installs
or replaces anything.

Why this exists: the bot reported an ad page (``pipefitter.in/...``) for a
VPLink, and the only trail line in the log was that same ad page - no auth
URL anywhere. A local replica *did* show the auth URL in the trail, so live
behaviour differs from the replica and guessing further is pointless. This
script answers one question with evidence instead of theory:

    does the auth URL ever reach the browser we drive, and if so, through
    which channel does it arrive?

It observes, it does not bypass: no CAPTCHA solving, no challenge handling,
no login, no header tricks. It drives the page the way a person would.

    python3 tools/trace_live.py https://vplink.in/nAKfh

Output has three parts:

1. ``[env]``   - which browser module is actually imported (path + sha256 +
   engine name), so a phone running a different file than the repo shows up
   immediately.
2. ``[trace]`` - a timeline of every navigation with millisecond offsets, from
   four independent channels: Playwright page events, Chrome DevTools Protocol
   network events, ``page.url`` polling at 200 ms, and popup/frame commits.
3. ``[verdict]`` - one of A/B/C/D/E with the evidence lines behind it.

Exit code is 0 whenever the trace ran, whatever the verdict.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The engine lives outside this package; ask the bot's own config where.
def _add_engine_path() -> str:
    cand = ""
    try:
        from nickbot.config import Config
        cand = str(Config.load().linkresolver_home)
    except Exception:
        cand = str(ROOT.parent / "linkresolver")
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)
    return cand


ENGINE_HOME = _add_engine_path()

DEFAULT_URL = "https://vplink.in/nAKfh"
POLL_MS = 200          # our sampling
BOT_POLL_MS = 900      # browser.py samples page.url about this often


# --------------------------------------------------------------------- output

def hr(title: str = "") -> None:
    print("\n== " + (title + " " if title else "").ljust(70, "="), flush=True)


def out(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------- identity

def engine_identity() -> Dict[str, Any]:
    """Which browser.py is on sys.path, and what it is written against."""
    info: Dict[str, Any] = {"path": "", "sha256": "", "engine": "unknown",
                            "playwright": "", "selenium": "", "chromium": ""}
    try:
        import linkresolver.browser as B
    except Exception as exc:
        info["engine"] = f"import failed: {type(exc).__name__}: {exc}"
        out(f"[env] cannot import linkresolver.browser: {info['engine']}")
        return info
    path = pathlib.Path(getattr(B, "__file__", "") or "")
    info["path"] = str(path)
    if path.is_file():
        raw = path.read_bytes()
        info["sha256"] = hashlib.sha256(raw).hexdigest()[:16]
        src = raw.decode("utf-8", "replace")
        has_pw = "playwright" in src
        has_se = "selenium" in src or "webdriver" in src
        info["engine"] = ("playwright" if has_pw and not has_se
                          else "selenium" if has_se and not has_pw
                          else "mixed" if has_pw and has_se else "unknown")
    try:
        from importlib.metadata import version
        info["playwright"] = version("playwright")
    except Exception:
        info["playwright"] = "absent"
    try:
        from importlib.metadata import version
        info["selenium"] = version("selenium")
    except Exception:
        info["selenium"] = "absent"
    for cand in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                 "/usr/bin/google-chrome",
                 "/data/data/com.termux/files/usr/bin/chromium"):
        if pathlib.Path(cand).exists():
            info["chromium"] = cand
            break
    import platform
    out(f"[env] python {platform.python_version()} on {platform.machine()}")
    out(f"[env] browser.py -> {info['path']}")
    out(f"[env] sha256 {info['sha256']}  engine={info['engine']}")
    out(f"[env] playwright={info['playwright']} selenium={info['selenium']} "
        f"chromium={info['chromium'] or 'playwright default'}")
    return info


# --------------------------------------------------------------------- tracer

class Trace:
    """One instrumented browser run."""

    def __init__(self, url: str, timeout: float, stub_window_open: bool,
                 capture_popups: bool, label: str) -> None:
        self.url = url
        self.timeout = timeout
        self.stub = stub_window_open
        self.capture_popups = capture_popups
        self.label = label
        self.t0 = time.time()
        self.events: List[Dict[str, Any]] = []
        self.watched: set = set()
        self.final = ""
        self.note = ""
        self.goto_ok = False

    # -- recording -------------------------------------------------------
    def rec(self, ch: str, url: str, **extra: Any) -> None:
        ms = int((time.time() - self.t0) * 1000)
        self.events.append({"t": ms, "ch": ch, "url": url, **extra})

    def dump(self) -> None:
        out(f"\n[trace:{self.label}] timeline (ms since goto)")
        last = None
        for e in self.events:
            key = (e["ch"], e["url"])
            if key == last:
                continue
            last = key
            extra = " ".join(f"{k}={v}" for k, v in e.items()
                             if k not in ("t", "ch", "url") and v not in ("", None))
            out(f"  {e['t']:>6}ms  {e['ch']:<13} {e['url']}" + (f"   [{extra}]" if extra else ""))

    # -- one page, all channels ------------------------------------------
    def watch(self, page: Any, tag: str) -> None:
        key = id(page)
        if key in self.watched:
            return
        self.watched.add(key)

        def on_nav(frame: Any) -> None:
            try:
                u = frame.url or ""
                is_main = frame == page.main_frame
                name = "main" if is_main else (getattr(frame, "name", "") or "sub")
            except Exception:
                return
            if not u:
                return
            if is_main:
                ch = "commit-main" if tag == "main" else "commit-popup"
            else:
                ch = "commit-frame"
            self.rec(ch, u, tag=tag, frame=name)

        def on_req(req: Any) -> None:
            try:
                if req.resource_type != "document":
                    return
                prev, r, n = "", req.redirected_from, 0
                while r is not None and n < 8:
                    prev = r.url
                    r = r.redirected_from
                    n += 1
                self.rec("req-doc", req.url, tag=tag, frm=prev[:70] or "-")
            except Exception:
                pass

        def on_resp(resp: Any) -> None:
            try:
                if not (300 <= resp.status < 400):
                    return
                loc = resp.headers.get("location", "") or ""
                self.rec("302", urljoin(resp.url, loc) if loc else resp.url,
                         tag=tag, status=resp.status, frm=resp.url[:70])
            except Exception:
                pass

        page.on("framenavigated", on_nav)
        page.on("request", on_req)
        page.on("response", on_resp)

        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Page.enable")

            def cdp_req(ev: Any) -> None:
                try:
                    if ev.get("type") != "Document":
                        return
                    red = ev.get("redirectResponse") or {}
                    self.rec("cdp-doc", (ev.get("request") or {}).get("url", ""),
                             tag=tag, status=red.get("status", ""),
                             frm=(red.get("url") or "")[:70])
                except Exception:
                    pass

            def cdp_nav(ev: Any) -> None:
                try:
                    f = ev.get("frame", {})
                    self.rec("cdp-nav", f.get("url", ""), tag=tag,
                             frame=(f.get("name") or (f.get("id") or "")[:8]))
                except Exception:
                    pass

            cdp.on("Network.requestWillBeSent", cdp_req)
            cdp.on("Page.frameNavigated", cdp_nav)
        except Exception as exc:
            self.rec("note", self.url, detail=f"CDP unavailable: {type(exc).__name__}")

    # -- run -------------------------------------------------------------
    def run(self) -> None:
        from linkresolver.browser import (BLOCK_HOSTS, CLICK_RX, JS_CLICK, JS_FIND,
                                          UA, BrowserResolver, _gate_hosts)
        from linkresolver.rules import host_of
        from playwright.sync_api import sync_playwright

        gates = _gate_hosts(host_of(self.url))

        with sync_playwright() as p:
            # Same launch path as the bot, so engine and flags are identical.
            browser = BrowserResolver(headless=True, timeout=self.timeout)._launch(p)
            ctx = browser.new_context(user_agent=UA, locale="en-US",
                                      viewport={"width": 1366, "height": 800},
                                      ignore_https_errors=True)
            if self.stub:
                ctx.add_init_script(
                    "window.open = function(){ return {focus(){}, close(){}, closed:true}; };")
            else:
                # Record the call, then let it behave normally.
                ctx.add_init_script(
                    "window.__opened = [];"
                    "var _o = window.open;"
                    "window.open = function(u){ window.__opened.push(String(u||''));"
                    "  return _o.apply(window, arguments); };")

            page = ctx.new_page()
            page.set_default_timeout(15000)

            # Attached after the main tab exists, so it only ever sees real
            # popups (a listener attached earlier also fires for our own tab).
            if self.capture_popups:
                def on_popup(pg: Any) -> None:
                    if pg is page:
                        return
                    self.rec("popup-open", pg.url or "(blank)")
                    self.watch(pg, tag="popup")
                ctx.on("page", on_popup)

            def route(r: Any) -> None:
                try:
                    h = host_of(r.request.url)
                    if any(h == b or h.endswith("." + b) or b in h for b in BLOCK_HOSTS):
                        return r.abort()
                except Exception:
                    pass
                return r.continue_()

            ctx.route("**/*", route)
            self.watch(page, tag="main")

            self.rec("goto", self.url)
            try:
                page.goto(self.url, wait_until="domcontentloaded",
                          timeout=int(self.timeout * 1000 * 0.6))
                self.goto_ok = True
            except Exception as exc:
                self.rec("note", self.url, detail=f"goto {type(exc).__name__}")

            deadline = time.time() + self.timeout
            clicked: set = set()
            last_url = ""

            while time.time() < deadline:
                cur = page.url or ""
                if cur != last_url:
                    self.rec("poll", cur, tag="main")
                    last_url = cur
                    if cur and cur != self.url:
                        ch = host_of(cur)
                        if ch and not any(ch == g or ch.endswith("." + g) for g in gates):
                            self.final = cur
                            break
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
                        self.rec("click", cur, label=label)
                        acted = True
                        page.wait_for_timeout(1200)
                        break
                if not acted:
                    page.wait_for_timeout(POLL_MS)

            if not self.final:
                self.note = "never left the gate hosts"
                self.final = page.url or ""
            else:
                self.note = "left the gate hosts"

            # What the page's own JS thinks happened
            try:
                for e in page.evaluate(
                        "() => (performance.getEntriesByType('navigation')||[]).map(e => "
                        "{n: e.name, t: e.type, r: e.redirectCount})") or []:
                    self.rec("perf-nav", e.get("n", ""), type=e.get("t"),
                             redirects=e.get("r"))
            except Exception:
                pass
            try:
                for u in page.evaluate("() => window.__opened || []") or []:
                    self.rec("window-open", u)
            except Exception:
                pass

            ctx.close()
            browser.close()


# ---------------------------------------------------------------- bot itself

def bot_path(url: str, timeout: float) -> Any:
    """Run the bot's own chain, printing the URL each stage is handed.

    The browser trail in the live log held the ad page and nothing else - not
    even the gate. That only happens when the browser is *started* on some
    other URL, so the decisive value is the argument ``BrowserResolver`` gets,
    not what it does afterwards. Nothing here changes the bot: the two
    resolvers are wrapped, called, and unwrapped.
    """
    hr("bot path: the exact chain behind the Telegram reply")
    try:
        from nickbot.config import Config
        from nickbot.gates import FastResolver
        from nickbot.resolver import Engine
        from linkresolver import browser as B
    except Exception as exc:
        out(f"[bot] unavailable: {type(exc).__name__}: {exc}")
        return [], [], ""

    cfg = Config.load()
    cfg.allow_browser = True
    cfg.browser_timeout = timeout
    cfg.proxy = ""
    cfg.proxy_farm = False
    cfg.auto_proxy = False

    orig_http = FastResolver.resolve
    orig_br = B.BrowserResolver.resolve
    browser_inputs: List[str] = []
    seen: Dict[str, Any] = {"steps": [], "final": ""}

    async def http(self: Any, u: str) -> Dict[str, Any]:
        d = await orig_http(self, u)
        out(f"[bot] HTTP   in={u}")
        out(f"[bot]        out={d.get('final') or '-'}  ok={d.get('ok')}  "
            f"blocked={d.get('blocked') or '-'}  needs_browser={d.get('needs_browser')}")
        return d

    def br(self: Any, u: str) -> Any:
        browser_inputs.append(u)
        out(f"[bot] BROWSER INPUT = {u}")
        final, steps, note = orig_br(self, u)
        seen["final"] = final or ""
        seen["steps"] = [getattr(s, "url", "") for s in steps]
        out(f"[bot] BROWSER OUTPUT = {final or '-'}   ({note})")
        for s in steps:
            out(f"[bot]        {getattr(s, 'kind', '?'):<9} {getattr(s, 'url', '')}")
        return final, steps, note

    FastResolver.resolve = http          # type: ignore[assignment]
    B.BrowserResolver.resolve = br       # type: ignore[assignment]
    try:
        import asyncio
        e = Engine(cfg)
        data = asyncio.run(e.resolve(url, fresh=True))
    except Exception as exc:
        out(f"[bot] failed: {type(exc).__name__}: {exc}")
        return browser_inputs, seen["steps"], seen["final"]
    finally:
        FastResolver.resolve = orig_http     # type: ignore[assignment]
        B.BrowserResolver.resolve = orig_br  # type: ignore[assignment]

    out(f"[bot] result  final={data.get('final') or '-'}")
    out(f"[bot]         status={data.get('status')}  method={data.get('method')}  "
        f"ok={data.get('ok')}  hops={len(data.get('hops') or [])}")
    out(f"[bot]         note={data.get('note') or '-'}")
    for h in data.get("hops") or []:
        out(f"[bot]   hop {h.get('hop')}: {h.get('url')} -> {h.get('next')}  "
            f"[{h.get('method')} {h.get('action')} {h.get('why') or ''}]")
    if not browser_inputs:
        out("[bot] browser never ran on this chain - the HTTP path answered alone")
    else:
        out(f"[bot] browser was handed: {browser_inputs}")
    return browser_inputs, seen["steps"], seen["final"]


# ------------------------------------------------------------------- analysis

AUTHISH = ("auth", "pwasmultiverse", "generate?code", "code=")


def authish(u: str) -> bool:
    low = (u or "").lower()
    if not low or low.startswith("about:"):
        return False
    try:
        from nickbot import hops
        if hops.is_stop_endpoint(u):
            return True
    except Exception:
        pass
    return any(k in low for k in AUTHISH)


def hits_by_channel(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    seen: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        if authish(e.get("url", "")):
            seen.setdefault(e["ch"], []).append(e)
    return seen


def dwell_ms(events: List[Dict[str, Any]], t_hit: int) -> int:
    """How long the auth URL stayed the main-frame URL."""
    later = [e["t"] for e in events
             if e["ch"] in ("commit-main", "poll") and e["t"] > t_hit]
    return (min(later) - t_hit) if later else -1


def verdict(tr: Trace, baseline_final: str, baseline_steps: List[str]) -> str:
    ev = tr.events
    hits = hits_by_channel(ev)
    hr("verdict")
    out(f"[verdict] baseline final : {baseline_final or '-'}")
    out(f"[verdict] baseline steps : {len(baseline_steps)} step(s)")
    for u in baseline_steps:
        out(f"[verdict]    {u}")
    out(f"[verdict] auth-ish URL seen in: { {k: len(v) for k, v in hits.items()} or 'NO CHANNEL'}")

    def first(ch: str) -> Dict[str, Any]:
        v = hits.get(ch) or []
        return v[0] if v else {}

    main_c = first("commit-main")
    sub_c = first("commit-frame")
    pop_c = first("commit-popup")
    win_o = first("window-open")
    s302 = first("302")
    doc = first("req-doc")
    cdp = first("cdp-doc")

    in_baseline = any(authish(u) for u in baseline_steps)
    base_auth = authish(baseline_final)

    case = "E"
    why = ""
    if main_c:
        d = dwell_ms(ev, main_c["t"])
        if in_baseline and base_auth:
            case = "OK"
            why = ("the main frame committed the auth URL and the resolver already "
                   "returns it - nothing to fix on this link")
        elif in_baseline and not base_auth:
            case = "C"
            why = (f"steps hold the auth URL but the run ended on {baseline_final} - "
                   "the return value, not the trail, is what got used")
        elif d >= 0 and d < BOT_POLL_MS:
            case = "C-miss"
            why = (f"the auth URL was the real main-frame URL for only {d}ms; "
                   f"browser.py samples page.url every ~{BOT_POLL_MS}ms and stepped over it")
        else:
            case = "C"
            why = (f"the main frame did commit the auth URL ({d}ms dwell) but it never "
                   "reached the returned steps - the history is being dropped")
    elif s302:
        case = "B"
        why = ("the auth URL exists only as a redirect target: Chromium passed "
               "through it and committed straight to the next page, so "
               "framenavigated never reports it")
    elif sub_c:
        case = "D"
        why = ("the auth URL was committed by a sub-frame; browser.py drops every "
               "frame that is not the main frame (browser.py:159)")
    elif pop_c or win_o or hits.get("popup-open"):
        case = "A"
        why = ("the auth URL was only opened as a popup / window.open - the page "
               "we watch never navigated to it")
    elif doc or cdp:
        case = "D"
        why = ("the auth URL was requested as a document but never navigated to; "
               "it is built in JS and fetched, not visited")
    else:
        case = "E"
        why = ("no channel saw an auth-ish URL at all: this browser received a "
               "different chain than the HTTP client did")

    out(f"[verdict] CASE {case}: {why}")
    out(f"[verdict]   main-commit   : {main_c.get('url', '-')} @{main_c.get('t', '-')}ms")
    out(f"[verdict]   subframe      : {sub_c.get('url', '-')} @{sub_c.get('t', '-')}ms")
    out(f"[verdict]   popup         : {pop_c.get('url', '-')} @{pop_c.get('t', '-')}ms")
    out(f"[verdict]   window.open   : {win_o.get('url', '-')} @{win_o.get('t', '-')}ms")
    out(f"[verdict]   302 target    : {s302.get('url', '-')} @{s302.get('t', '-')}ms")
    out(f"[verdict]   document req  : {doc.get('url', '-')} @{doc.get('t', '-')}ms")
    out(f"[verdict]   run ended on  : {tr.final} ({tr.note})")
    out("TRACE-SUMMARY " + json.dumps({
        "case": case,
        "final": tr.final,
        "auth_seen_in": sorted(hits.keys()),
        "events": len(ev),
        "goto_ok": tr.goto_ok,
        "stub_window_open": tr.stub,
    }, ensure_ascii=False))
    return case


# ------------------------------------------------------------ selenium engine

class SeleniumHooks:
    """Wrap Selenium so every navigation point prints the real URL.

    Nothing here changes what the page does: ``get`` still navigates, ``click``
    still clicks, ``quit`` still quits. The wrappers only read and print. The
    one addition is ``goog:loggingPrefs``, which makes ChromeDriver keep its own
    DevTools event log - that log is the only place a 302 ``Location``, a
    ``meta refresh`` or the *reason* a navigation started is recorded.
    """

    def __init__(self) -> None:
        self.t0 = time.time()
        self.events: List[Dict[str, Any]] = []
        self.drivers: List[Any] = []
        self.net: List[Dict[str, Any]] = []
        self.first_html = ""
        self.first_html_url = ""
        self._last = ""
        self._installed = False

    def rec(self, ch: str, url: str = "", **extra: Any) -> None:
        ms = int((time.time() - self.t0) * 1000)
        if ch == "current_url" and url == self._last:
            return
        if ch == "current_url":
            self._last = url
        self.events.append({"t": ms, "ch": ch, "url": url, **extra})
        tail = "".join(f"  [{k}={v}]" for k, v in extra.items() if v not in ("", None))
        out(f"[browser] {ch} = {url}" + tail)

    # -- hooks -----------------------------------------------------------
    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        try:
            from selenium.webdriver.chrome.webdriver import WebDriver as Chrome
            from selenium.webdriver.remote.webdriver import WebDriver
            from selenium.webdriver.remote.webelement import WebElement
        except Exception as exc:
            out(f"[browser] selenium import failed: {type(exc).__name__}: {exc}")
            return

        raw_prop = WebDriver.current_url
        raw_get_url = raw_prop.fget

        def cur(self: Any) -> str:
            v = raw_get_url(self)
            self_hooks.rec("current_url", v)
            return v

        WebDriver.current_url = property(cur)

        orig_get = WebDriver.get

        def get(self: Any, target: str) -> None:
            self_hooks.rec("driver.get INPUT", target)
            orig_get(self, target)
            try:
                self_hooks.rec("current_url AFTER get", raw_get_url(self))
            except Exception:
                pass
            # Snapshot the gate document before it navigates away: this is the
            # only moment the server-sent HTML still exists.
            if not self_hooks.first_html:
                try:
                    self_hooks.first_html = self.page_source or ""
                    self_hooks.first_html_url = raw_get_url(self)
                except Exception:
                    pass

        WebDriver.get = get            # type: ignore[assignment]

        orig_click = WebElement.click

        def click(self: Any) -> Any:
            text = ""
            try:
                text = (self.text or "").strip().replace("\n", " ")[:40]
            except Exception:
                pass
            where = ""
            try:
                where = raw_get_url(self._parent)
            except Exception:
                pass
            self_hooks.rec("click", where, label=text)
            return orig_click(self)

        WebElement.click = click      # type: ignore[assignment]

        orig_init = Chrome.__init__

        def init(self: Any, *a: Any, **kw: Any) -> None:
            opts = kw.get("options")
            if opts is None:
                for v in a:
                    if hasattr(v, "set_capability"):
                        opts = v
                        break
            if opts is not None:
                try:
                    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
                except Exception as exc:
                    self_hooks.rec("note", "", detail=f"loggingPrefs rejected: {exc}")
            orig_init(self, *a, **kw)
            self_hooks.drivers.append(self)
            self_hooks.rec("driver started", getattr(self, "capabilities", {}).get(
                "browserVersion", "") or "")

        Chrome.__init__ = init        # type: ignore[assignment]

        orig_quit = WebDriver.quit

        def quit_(self: Any) -> None:
            try:
                self_hooks.rec("current_url BEFORE RETURN", raw_get_url(self))
            except Exception:
                pass
            self_hooks.collect(self)
            return orig_quit(self)

        WebDriver.quit = quit_        # type: ignore[assignment]
        self.rec("hooks installed", "")

    # -- devtools log ----------------------------------------------------
    def collect(self, drv: Any) -> None:
        try:
            entries = drv.get_log("performance")
        except Exception as exc:
            self.rec("note", "", detail=f"performance log unavailable: {type(exc).__name__}")
            return
        msgs: List[Dict[str, Any]] = []
        for e in entries or []:
            try:
                msgs.append(json.loads(e.get("message", "{}")).get("message", {}))
            except Exception:
                continue
        stamps = [(m.get("params") or {}).get("timestamp") for m in msgs]
        nums = [s for s in stamps if isinstance(s, (int, float))]
        base = min(nums) if nums else 0.0
        state = {"last": base}

        def rel(p: Dict[str, Any]) -> int:
            ts = p.get("timestamp")
            if isinstance(ts, (int, float)):
                state["last"] = ts
            # Some Page.* events carry no timestamp; they inherit the last one
            # seen so the order stays readable instead of showing -1.
            return int((state["last"] - base) * 1000)

        out("[net] timeline (ms since the first devtools event)")
        for m in msgs:
            meth = m.get("method", "")
            p = m.get("params") or {}
            t = rel(p)
            if meth == "Network.requestWillBeSent":
                req = p.get("request") or {}
                url = req.get("url", "")
                typ = p.get("type", "")
                red = p.get("redirectResponse") or {}
                init = (p.get("initiator") or {}).get("type", "")
                self.net.append({"kind": "req", "t": t, "url": url, "type": typ,
                                 "status": red.get("status"), "frm": red.get("url", ""),
                                 "initiator": init})
                if typ in ("Document", "XHR", "Fetch", "EventSource", "WebSocket") or red or authish(url):
                    bits = [f"[net] +{t}ms REQ", f"type={typ or '?'}"]
                    if init:
                        bits.append(f"initiator={init}")
                    if red:
                        bits.append(f"redirect_from={red.get('url', '')[:60]} status={red.get('status')}")
                    out(" ".join(bits) + f"  {url[:110]}")
            elif meth == "Network.responseReceived":
                r = p.get("response") or {}
                url = r.get("url", "")
                status = r.get("status")
                loc = ""
                for k, v in (r.get("headers") or {}).items():
                    if k.lower() == "location":
                        loc = v
                self.net.append({"kind": "resp", "t": t, "url": url,
                                 "status": status, "loc": loc, "type": p.get("type", "")})
                if p.get("type") in ("Document", "XHR", "Fetch") or authish(url) or (status and 300 <= status < 400):
                    out(f"[net] +{t}ms RESP status={status}  {url[:100]}"
                        + (f"  location={loc[:80]}" if loc else ""))
            elif meth == "Network.responseReceivedExtraInfo":
                status = p.get("status")
                headers = p.get("headers") or {}
                loc = headers.get("Location") or headers.get("location") or ""
                if loc:
                    self.net.append({"kind": "extra", "t": t, "url": loc,
                                     "status": status, "loc": loc})
                    out(f"[net] +{t}ms RAW status={status}  location={loc[:90]}")
            elif meth == "Page.frameRequestedNavigation":
                self.net.append({"kind": "nav-req", "t": t, "url": p.get("url", ""),
                                 "reason": p.get("reason", ""),
                                 "disposition": p.get("disposition", "")})
                out(f"[nav] +{t}ms REQUESTED reason={p.get('reason')} "
                    f"disposition={p.get('disposition')}  {p.get('url', '')[:100]}")
            elif meth == "Page.frameScheduledNavigation":
                self.net.append({"kind": "nav-sched", "t": t, "url": p.get("url", ""),
                                 "reason": p.get("reason", ""), "delay": p.get("delay")})
                out(f"[nav] +{t}ms SCHEDULED delay={p.get('delay')} "
                    f"reason={p.get('reason')}  {p.get('url', '')[:100]}")
            elif meth == "Page.frameNavigated":
                f = p.get("frame") or {}
                if not f.get("parentId"):          # main frame only
                    self.net.append({"kind": "nav-commit", "t": t, "url": f.get("url", "")})
                    out(f"[nav] +{t}ms COMMITTED  {f.get('url', '')[:100]}")

        # Anything auth-ish anywhere in the log, whatever the request type.
        probes = [r for r in self.net if authish(r.get("url", ""))]
        if probes:
            out("[auth-probe] auth-ish URL found in the devtools log:")
            for r in probes:
                out(f"[auth-probe]   +{r['t']}ms {r['kind']} type={r.get('type', '-')} "
                    f"initiator={r.get('initiator', '-')}  {r['url'][:100]}")
        else:
            out("[auth-probe] no auth-ish URL in ANY request type (document, xhr, fetch, "
                "script, iframe, redirect target)")

    # -- html evidence ---------------------------------------------------
    def scan_html(self, token: str, gate: str = "") -> None:
        """Report what the snapshot really is, then fetch the gate properly.

        ``driver.get`` blocks until the page has finished loading *including*
        its own script navigations, so by the time we can read ``page_source``
        the browser is already on the last page of the chain. The old label
        said "gate document" and then reported "no literal destination", which
        was reading the wrong page and calling it evidence. The snapshot is now
        labelled by the URL it actually came from, and the gate is fetched
        separately - same cookies and UA, no JavaScript - so a "the URL is a
        literal in the HTML" verdict means what it says.
        """
        h = self.first_html or ""
        if not h:
            out("[html] no snapshot captured")
        else:
            where = self.first_html_url or "-"
            same_page = bool(gate) and where.rstrip("/") == gate.rstrip("/")
            out(f"[html] page_source snapshot {len(h)} bytes from {where}")
            if not same_page:
                out("[html] that is the LAST page of the chain, not the gate - "
                    "driver.get() blocks through the script navigations, so no "
                    "gate snapshot was possible from page_source")
            self._scan(h, token, "html")

        if gate:
            self.fetch_gate(gate, token)

    def _scan(self, h: str, token: str, tag: str) -> None:
        n = h.lower().count(token.lower()) if token else 0
        out(f"[{tag}] '{token}' appears {n}x")
        if n:
            for m in list(re.finditer(re.escape(token), h, re.I))[:3]:
                s = max(0, m.start() - 90)
                snippet = re.sub(r"\s+", " ", h[s:m.end() + 90])
                out(f"[{tag}]   ...{snippet}...")
        for pat in ('location.href', 'window.location', 'location.replace',
                    'location.assign', 'http-equiv="refresh"', "http-equiv='refresh'",
                    'window.open', '.submit('):
            c = h.lower().count(pat.lower())
            if c:
                out(f"[{tag}] pattern {pat!r} x{c}")

    def fetch_gate(self, gate: str, token: str) -> None:
        """Fetch the gate HTML without executing JavaScript.

        This is the only way to tell "the destination URL is written in the
        page" (B) from "a script or an API call built it" (D): the browser's
        own copy is gone by the time we can read it. No JS runs here, so a
        Cloudflare challenge page may come back instead of the real document -
        that is reported as-is, not worked around.
        """
        try:
            import requests
        except Exception as exc:
            out(f"[gate-html] requests unavailable: {type(exc).__name__}")
            return
        ua = ""
        cookies = ""
        try:
            drv = self.drivers[-1] if self.drivers else None
            if drv is not None:
                ua = drv.execute_script("return navigator.userAgent") or ""
                cookies = "; ".join(f"{c['name']}={c['value']}"
                                    for c in drv.get_cookies())
        except Exception:
            pass
        try:
            r = requests.get(gate, timeout=20, allow_redirects=False,
                             headers={"User-Agent": ua or "Mozilla/5.0",
                                      "Accept": "text/html,*/*",
                                      "Cookie": cookies} if cookies else
                                     {"User-Agent": ua or "Mozilla/5.0",
                                      "Accept": "text/html,*/*"})
        except Exception as exc:
            out(f"[gate-html] fetch failed: {type(exc).__name__}: {exc}")
            return
        loc = r.headers.get("Location", "")
        out(f"[gate-html] GET {gate} -> {r.status_code} "
            f"{len(r.content)} bytes  content-type={r.headers.get('Content-Type', '-')}"
            + (f"  location={loc}" if loc else ""))
        if r.status_code in (301, 302, 303, 307, 308):
            out(f"[gate-html] the gate answers {r.status_code} directly - "
                f"the destination is in the Location header: {loc}")
            return
        body = r.text or ""
        low = body.lower()
        if "challenge-platform" in low or "cf-chl" in low or "just a moment" in low:
            out("[gate-html] this looks like a Cloudflare challenge page, so the "
                "document below is NOT the real gate HTML - no JS ran here")
        self._scan(body, token, "gate-html")
        code = gate.rstrip("/").rsplit("/", 1)[-1]
        if code and len(code) <= 24 and code != token:
            out(f"[gate-html] gate code {code!r} appears {low.count(code.lower())}x "
                f"in the document")
        for pat in ("insurancesstudy", "uiso=", "location =", "location="):
            c = low.count(pat.lower())
            if c:
                out(f"[gate-html] pattern {pat!r} x{c}")

    # -- mechanism -------------------------------------------------------
    def chain(self, gate: str) -> List[Dict[str, str]]:
        """Walk gate -> ... -> destination using the devtools events.

        Two kinds of edge exist: a navigation *request* (script, meta refresh,
        link, form) and an HTTP redirect (302 with a Location). Keeping them in
        one ordered list is what makes the real mechanism visible.
        """
        def same(a: str, b: str) -> bool:
            return bool(a) and bool(b) and a.rstrip("/") == b.rstrip("/")

        def navigable(u: str) -> bool:
            # about:blank and the blocked-ad frames are not steps in the chain;
            # neither is a subresource (a 302 on a script is not a navigation).
            return bool(u) and u.startswith(("http://", "https://"))

        edges: List[Dict[str, str]] = []
        cur = gate
        for r in self.net:
            if r["kind"] == "nav-req" and navigable(r.get("url", "")) \
                    and not same(r["url"], cur):
                edges.append({"from": cur, "to": r["url"],
                              "how": r.get("reason") or "navigation"})
                cur = r["url"]
            elif r["kind"] == "req" and r.get("type") == "Document" \
                    and navigable(r["url"]) and r.get("frm") \
                    and not same(r["url"], r["frm"]):
                edges.append({"from": r["frm"], "to": r["url"],
                              "how": f"http{r.get('status')}"})
                if same(r["frm"], cur):
                    cur = r["url"]
        return edges

    def mechanism(self, dest: str, gate: str) -> Tuple[str, str]:
        edges = self.chain(gate)
        if not edges:
            return "F", "no navigation edge recorded at all"

        first = edges[0]
        how = first["how"]
        tgt = first["to"]
        rest = [e["how"] for e in edges[1:]]
        tail = f" (+{len(rest)} more hop(s): {', '.join(rest[:4])})" if rest else ""

        if how.startswith("http3") or how.startswith("http2"):
            return "A", (f"the gate document itself answered {how[4:]} with a Location "
                         f"to {tgt[:70]}{tail}")
        if how == "metaTagRefresh":
            return "C", f"meta refresh to {tgt[:70]}{tail}"
        if how == "scriptInitiated":
            literal = bool(tgt) and tgt in self.first_html
            if not literal and dest:
                literal = dest in self.first_html
            if literal:
                return "B", ("the target URL is a literal in the gate HTML and a "
                             f"script navigated to it{tail}")
            return "D", ("the gate's own script navigated to a URL that is NOT a "
                         f"literal in the gate HTML - it was built at runtime: "
                         f"{tgt[:70]}{tail}")
        if how in ("link", "formSubmission", "linkClicked"):
            return "E", f"a {how} started the navigation to {tgt[:70]}{tail}"
        return "E", f"navigation started by {how} to {tgt[:70]}{tail}"

    def chain_line(self, gate: str, dest: str) -> str:
        edges = self.chain(gate)
        if not edges:
            return "(no navigation edges)"
        parts = [gate or "?"]
        for e in edges:
            parts.append(f"--{e['how']}--> {e['to']}")
        return "  ".join(parts)

    # -- summary ---------------------------------------------------------
    def summary(self, inputs: List[str], steps: List[str], final: str) -> str:
        hr("verdict")
        skip = {"direct resolve INPUT", "hooks installed", "driver started", "note"}
        seen: Dict[str, List[str]] = {}
        for e in self.events:
            if e["ch"] in skip:
                continue
            if authish(e.get("url", "")):
                seen.setdefault(e["ch"], []).append(e["url"])
        for r in self.net:
            if authish(r.get("url", "")):
                seen.setdefault("devtools:" + r["kind"], []).append(r["url"])

        gate = inputs[0] if inputs else ""
        dest = final or (steps[-1] if steps else "")
        out(f"[verdict] browser input(s): {inputs or '-'}")
        out(f"[verdict] browser output  : {final or '-'}")
        out(f"[verdict] steps           : {steps or '-'}")
        out(f"[verdict] auth URL seen in: { {k: len(v) for k, v in seen.items()} or 'NO CHANNEL'}")

        handoff_auth = any(authish(u) for u in inputs)
        out(f"[verdict] HANDOFF = {'AUTH_URL' if handoff_auth else 'GATE_URL (or other)'}")

        if handoff_auth:
            case, why = "A", ("the browser was handed the auth URL and navigated away from it")
        elif "current_url" in seen:
            case, why = "C", "the auth URL was the live current_url but never reached steps"
        elif "devtools:req" in seen or "devtools:resp" in seen:
            case, why = "B", "the auth URL appeared only as a redirect target inside the run"
        else:
            case, why = "E", "no channel saw an auth URL at all on this device"
        out(f"[verdict] CASE {case}: {why}")

        mech, detail = self.mechanism(dest, gate)
        out(f"[verdict] MECHANISM = {mech}: {detail}")
        out(f"[verdict] CHAIN = {self.chain_line(gate, dest)}")
        if dest:
            try:
                from urllib.parse import urlparse
                self.scan_html(
                    urlparse(dest).hostname.split(".")[0] if urlparse(dest).hostname else "",
                    gate=gate,
                )
            except Exception:
                pass

        out("TRACE-SUMMARY " + json.dumps({
            "engine": "selenium", "case": case, "mechanism": mech,
            "handoff": "auth" if handoff_auth else "gate",
            "gate": gate, "destination": dest,
            "auth_seen_in": sorted(seen.keys()), "events": len(self.events),
        }, ensure_ascii=False))
        return case


self_hooks: SeleniumHooks


def dump_device_source(target: str) -> int:
    """Write the browser.py that is actually installed on this device.

    The phone's file is not in git (its sha256 matches no commit), so every
    change to it has been written blind. This copies the real file out so it
    can be committed, diffed and patched like any other source file.
    """
    import hashlib
    import inspect
    try:
        from linkresolver.browser import BrowserResolver
        path = inspect.getsourcefile(BrowserResolver) or ""
        whole = inspect.getsource(sys.modules[BrowserResolver.__module__])
    except Exception as exc:
        out(f"[dump] failed: {type(exc).__name__}: {exc}")
        return 2
    data = whole.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()[:16]
    lines = whole.count("\n") + 1
    out(f"[dump] {path}")
    out(f"[dump] {lines} lines, {len(data)} bytes, sha256 {sha}")
    if target == "-":
        sys.stdout.write(whole)
        return 0
    p = pathlib.Path(target).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(whole, encoding="utf-8")
    out(f"[dump] wrote {p}")
    out("[dump] commit it: git add -f " + target)
    return 0


def selenium_source_dump() -> None:
    """Print the implementation actually loaded on this device."""
    import inspect
    from linkresolver.browser import BrowserResolver
    hr("source on this device")
    try:
        src = inspect.getsource(BrowserResolver.resolve)
    except Exception as exc:
        out(f"[src] unavailable: {type(exc).__name__}: {exc}")
        return
    lines = src.splitlines()
    out(f"[src] BrowserResolver.resolve, {len(lines)} lines")
    for i, line in enumerate(lines[:110], 1):
        out(f"[src] {i:>3}| {line}")
    if len(lines) > 110:
        out(f"[src] ... {len(lines) - 110} more lines")
    marks = ("driver.get", "current_url", "implicitly_wait", ".click(", "sleep(",
             "window_handles", "switch_to", "page_source", "get_log")
    out("[src] navigation points:")
    for i, line in enumerate(lines, 1):
        for m in marks:
            if m in line:
                out(f"[src]   {i:>3}| {line.strip()[:110]}")
                break


def selenium_run(url: str, timeout: float, repeat: int = 1) -> int:
    global self_hooks
    selenium_source_dump()
    self_hooks = SeleniumHooks()
    hr("instrumented run (selenium)")
    self_hooks.install()
    inputs, steps, final = bot_path(url, timeout)
    if not inputs:
        # The bot answered over HTTP, so the browser never ran. Drive it once
        # directly, otherwise there is nothing to observe.
        out("[bot] browser never ran through the bot - running it directly once")
        try:
            from linkresolver.browser import BrowserResolver
            self_hooks.rec("direct resolve INPUT", url)
            final, bsteps, note = BrowserResolver(headless=True, timeout=timeout).resolve(url)
            steps = [getattr(s, "url", "") for s in bsteps]
            inputs = [url]
            out(f"[browser] direct resolve OUTPUT = {final or '-'}   ({note})")
        except Exception as exc:
            out(f"[browser] direct resolve failed: {type(exc).__name__}: {exc}")
    case = self_hooks.summary(inputs, steps, final)

    if repeat > 1:
        hr(f"repeat x{repeat}: does the destination stay the same?")
        seen: List[str] = []
        from linkresolver.browser import BrowserResolver
        for i in range(repeat):
            try:
                f, _s, _n = BrowserResolver(headless=True, timeout=timeout).resolve(url)
                seen.append(f or "-")
                out(f"[repeat] run {i + 1}: {f or '-'}")
            except Exception as exc:
                seen.append(f"error:{type(exc).__name__}")
                out(f"[repeat] run {i + 1}: failed {type(exc).__name__}")
        uniq = len({s.rstrip("/") for s in seen})
        out(f"[verdict] DESTINATION {'VARIES' if uniq > 1 else 'STABLE'} "
            f"({uniq} distinct in {len(seen)} runs)")

    hr("done")
    out(f"case={case}  - send the whole output back.")
    return 0

# ----------------------------------------------------------------------- main

def main(argv: List[str]) -> int:
    args = [a for a in argv[1:]]
    repeat = 1
    if "--repeat" in args:
        i = args.index("--repeat")
        try:
            repeat = max(1, min(5, int(args[i + 1])))
        except Exception:
            repeat = 2
        del args[i:i + 2]
    if "--dump-src" in args:
        i = args.index("--dump-src")
        target = args[i + 1] if len(args) > i + 1 and not args[i + 1].startswith("--") else "-"
        del args[i:i + (1 if target == "-" else 2)]
        return dump_device_source(target)
    url = args[0] if args else DEFAULT_URL
    timeout = float(args[1]) if len(args) > 1 else 30.0

    hr("environment")
    ident = engine_identity()
    if ident["engine"].startswith("import failed"):
        return 2
    if ident["engine"] == "selenium":
        out("[env] Selenium engine on this device - tracing it as it is, not "
            "the Playwright copy in the repo.")
        return selenium_run(url, timeout, repeat)
    if ident["engine"] != "playwright":
        out(f"[env] unrecognised engine ({ident['engine']}); refusing to guess.")
        return 2

    hr("baseline: what the bot itself returns")
    baseline_final, baseline_steps = "", []
    try:
        from linkresolver.browser import BrowserResolver
        final, steps, note = BrowserResolver(headless=True, timeout=timeout).resolve(url)
        baseline_final = final or ""
        baseline_steps = [getattr(s, "url", "") for s in steps]
        out(f"[baseline] final = {final or '-'}")
        out(f"[baseline] note  = {note}")
        for s in steps:
            out(f"[baseline]   {getattr(s, 'kind', '?'):<9} {getattr(s, 'url', '')}"
                f"   {getattr(s, 'detail', '')}")
    except Exception as exc:
        out(f"[baseline] failed: {type(exc).__name__}: {exc}")

    hr("instrumented run 1/2: bot flags (window.open stubbed)")
    tr = Trace(url, timeout, stub_window_open=True, capture_popups=False, label="stub")
    try:
        tr.run()
    except Exception as exc:
        out(f"[trace:stub] failed: {type(exc).__name__}: {exc}")
        return 1
    tr.dump()
    case = verdict(tr, baseline_final, baseline_steps)

    # Only worth a second pass when the stub may be the thing hiding the hop.
    if not any(authish(e.get("url", "")) for e in tr.events):
        hr("instrumented run 2/2: real window.open, popups watched")
        tr2 = Trace(url, timeout, stub_window_open=False, capture_popups=True, label="popups")
        try:
            tr2.run()
        except Exception as exc:
            out(f"[trace:popups] failed: {type(exc).__name__}: {exc}")
            return 1
        tr2.dump()
        case = verdict(tr2, baseline_final, baseline_steps)
    else:
        out("\n[skip] run 2 not needed: the auth URL already appeared with the stub on.")

    bot_path(url, timeout)

    hr("done")
    out(f"case={case}  - send the whole output back.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
