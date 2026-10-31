"""Command and message handlers.

Nothing in this file imports a Telegram framework: it works on ``Event``
objects from ``transport.py``, so the same code runs on python-telegram-bot
and on Kurigram.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import OrderedDict, deque
from typing import Deque, Dict, List, Optional

import httpx

from . import format as F
from . import sites
from .config import Config, set_env_value
from .db import DB
from .resolver import Engine
from .transport import Event, Sent

URL_RE = re.compile(r"https?://[^\s<>\"'）)\]]+", re.I)
BARE_RE = re.compile(r"(?<![\w@.])((?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s<>\"'）)\]]*)", re.I)

TRIM = ".,;:!?\"'）)】」』》。，、；：！？"


def extract_urls(text: str, limit: int = 5) -> List[str]:
    text = text or ""
    found = [m.group(0) for m in URL_RE.finditer(text)]
    if len(found) < limit:
        for m in BARE_RE.finditer(URL_RE.sub(" ", text)):
            cand = m.group(1)
            if not cand.lower().startswith(("t.me/", "telegram.me/")):
                found.append("https://" + cand)
    out, seen = [], set()
    for u in found:
        u = u.rstrip(TRIM)
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= limit:
            break
    return out


class Runtime:
    """Shared process state: rate limit, traces, per-user mode."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.db = DB(cfg.db_path)
        self.engine = Engine(cfg)
        self.started = time.time()
        self._hits: Dict[int, Deque[float]] = {}
        self._traces: "OrderedDict[str, dict]" = OrderedDict()
        self._mode: Dict[int, str] = {}          # auto | fast | browser

    # ---------------------------------------------------------- rate limit
    def allow(self, uid: int) -> tuple[bool, int]:
        now = time.time()
        q = self._hits.setdefault(uid, deque())
        while q and now - q[0] > self.cfg.rate_window:
            q.popleft()
        if len(q) >= self.cfg.rate_limit:
            return False, int(self.cfg.rate_window - (now - q[0])) + 1
        q.append(now)
        return True, 0

    # ---------------------------------------------------------- mode
    def mode(self, uid: int) -> str:
        return self._mode.get(uid, "auto")

    def set_mode(self, uid: int, mode: str) -> None:
        self._mode[uid] = mode

    def set_proxy(self, px: str) -> None:
        self.engine.set_proxy(px)
        set_env_value("PROXY", px)

    def allow_browser(self, uid: int) -> bool:
        if self.mode(uid) == "fast":
            return False
        return self.cfg.allow_browser

    # ---------------------------------------------------------- traces
    def put_trace(self, data: dict) -> str:
        key = "t:" + uuid.uuid4().hex[:10]
        self._traces[key] = data
        while len(self._traces) > 400:
            self._traces.popitem(last=False)
        return key

    def get_trace(self, key: str) -> Optional[dict]:
        return self._traces.get(key)


def rt(ev: Event) -> Runtime:
    return ev.rt


async def _guard(ev: Event) -> bool:
    if not ev.uid:
        return False
    r = ev.rt
    if r.db.is_banned(ev.uid):
        return False
    r.db.touch_user(ev.uid, ev.username, ev.first_name)
    return True


def owner_only(func):
    async def wrapper(ev: Event):
        if not ev.uid or not ev.rt.cfg.is_owner(ev.uid):
            await ev.reply(
                "🔒 That one is for the owner only.\n"
                "<i>Send a link instead — I'll take care of it.</i>"
            )
            return
        return await func(ev)

    wrapper.__name__ = func.__name__
    return wrapper


# ------------------------------------------------------------------ commands

async def cmd_start(ev: Event) -> None:
    if not await _guard(ev):
        return
    await ev.reply(F.help_text(ev.rt.cfg.is_owner(ev.uid)))


async def cmd_help(ev: Event) -> None:
    await cmd_start(ev)


async def cmd_id(ev: Event) -> None:
    await ev.reply(
        f"👤 You: <code>{ev.uid}</code>\n💬 This chat: <code>{ev.chat_id}</code>\n\n"
        "<i>Handy when you are setting the logger chat or adding yourself as owner.</i>"
    )


async def cmd_mode(ev: Event) -> None:
    if not await _guard(ev):
        return
    r = ev.rt
    arg = (ev.args[0].lower() if ev.args else "").strip()
    if arg in ("fast", "http"):
        r.set_mode(ev.uid, "fast")
        msg = ("⚡ <b>Fast mode</b>\n"
               "HTTP only. Quick, but a gate that runs a countdown will beat you.")
    elif arg in ("browser", "slow"):
        r.set_mode(ev.uid, "browser")
        msg = ("🌐 <b>Browser mode</b>\n"
               "A real Chromium opens the page. Slower, but it clicks through "
               "countdowns and popups that HTTP cannot touch.")
    elif arg in ("auto", ""):
        r.set_mode(ev.uid, "auto")
        msg = ("<b>Auto</b> it is.\n"
               "Fast path first, browser only if the gate actually needs one. "
               "This is what you want.")
    else:
        msg = ("Usage: <code>/mode auto</code> · <code>/mode fast</code> · "
               "<code>/mode browser</code>")
    await ev.reply(msg)


async def cmd_supported(ev: Event) -> None:
    """`/sites` lists everything, `/sites gplinks` looks one up."""
    q = " ".join(ev.args or []).strip()
    text = sites.search_text(q) if q else sites.supported_text()
    await ev.reply(text)


async def _farm_loop(rt_obj: Runtime) -> None:
    """Keep the pool warm, but gently and only when it is actually needed.

    The old version scanned 220 proxies the moment the process started; a few
    hundred SYNs filled the outbound socket table and Telegram long-polling
    timed out. Now it only refills after a real IP wall, and rarely.
    """
    await asyncio.sleep(300)          # let polling settle after a restart
    while True:
        engine = rt_obj.engine
        farm = engine.farm
        if farm is not None and engine.farm_needed:
            st = farm.stats()
            stale = st["fresh"] < max(3, st["total"] // 3)
            if st["total"] < 8 or stale:
                try:
                    await farm.refresh(limit=120, budget=40, workers=12)
                except Exception:
                    pass
        await asyncio.sleep(1800)


async def _reply_proxy_check(ev: Event, px: str, saved: bool = False) -> None:
    t0 = time.perf_counter()
    ip = country = ""
    err = ""
    try:
        async with httpx.AsyncClient(
            proxy=px, timeout=httpx.Timeout(12, connect=8), verify=False
        ) as c:
            resp = await c.get("https://ipinfo.io/json")
            data = resp.json()
            ip = str(data.get("ip") or "")
            country = str(data.get("country") or "")
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:90]}"
    took = time.perf_counter() - t0

    if err:
        text = (
            "⚠️ <b>Saved, but it doesn't answer</b>\n"
            f"<code>{F.h(px)}</code>\n\n"
            f"<i>{F.h(err)}</i>\n\n"
            "Check the host, port and password — or clear it with "
            "<code>/proxy clear</code>."
        )
    else:
        where = f" · {F.h(country)}" if country else ""
        text = (
            f"✅ <b>Proxy {'saved' if saved else 'is alive'}</b>\n"
            f"<code>{F.h(px)}</code>\n\n"
            f"Exit IP <code>{F.h(ip)}</code>{where} · answered in {took:.1f}s\n\n"
            "<i>Send the link again — gates that check the IP should let us through now.</i>"
        )
    await ev.reply(text)


@owner_only
async def cmd_proxy(ev: Event) -> None:
    r = ev.rt
    args = list(ev.args or [])
    arg = " ".join(args).strip()
    sub = (args[0].lower() if args else "")

    # ---------------------------------------------------- fixed proxy
    if arg.lower() in ("none", "clear", "reset"):
        r.set_proxy("")
        await ev.reply(
            "🛰 <b>Back to the server's own IP</b>\n\n"
            "<i>No custom exit anymore. If a gate starts refusing us again, "
            "<code>/proxy on IN</code> is the way back.</i>"
        )
        return

    # ---------------------------------------------------- farm switch
    if sub in ("on", "off") and len(args) <= 2:
        if sub == "off":
            r.engine.set_farm(False)
            set_env_value("PROXY_FARM", "false")
            await ev.reply(
                "🌾 <b>Pool off</b>\n\n"
                "<i>No more automatic IP rotation. Links that need a local IP will "
                "fail honestly instead of retrying.</i>"
            )
            return
        cc = (args[1].upper()[:2] if len(args) > 1 else r.cfg.proxy_country)
        r.engine.set_farm(True, cc)
        set_env_value("PROXY_FARM", "true")
        set_env_value("PROXY_COUNTRY", cc)
        st = r.engine.farm.stats() if r.engine.farm else {}
        await ev.reply(
            f"🌾 <b>Pool on · {F.h(cc)}</b>\n\n"
            f"{st.get('total', 0)} IPs in hand, {st.get('site_ok', 0)} already cleared "
            "a real gate.\n\n"
            "<i>It only kicks in when a site walls our IP — normal links don't pay "
            "for it.</i>\n"
            "<code>/proxy scan</code> fills the pool right now."
        )
        return

    # ---------------------------------------------------- scan
    if sub in ("scan", "refresh", "harvest"):
        cc = r.cfg.proxy_country
        target = ""
        for a in args[1:]:
            if len(a) == 2 and a.isalpha():
                cc = a.upper()
            elif a.startswith("http"):
                target = a
        if not r.engine.farm or r.engine.farm.country != cc:
            r.engine.set_farm(True, cc)
            set_env_value("PROXY_FARM", "true")
            set_env_value("PROXY_COUNTRY", cc)
        farm = r.engine.farm
        note = await ev.reply(
            f"🌾 <b>Looking for {F.h(cc)} proxies…</b>\n"
            "<i>harvest → ask each one for its exit IP → check the country"
            f"{' → open the site through it' if target else ''}</i>"
        )
        rep = await farm.refresh(target=target or None, limit=250, budget=55)
        st = farm.stats()
        await note.edit(F.scan_card(cc, rep, st, target))
        return

    # ---------------------------------------------------- list
    if sub in ("list", "ls", "show"):
        farm = r.engine.farm
        if not farm:
            await ev.reply(
                "🌾 <b>Pool is off</b>\n\nTurn it on with <code>/proxy on IN</code> first."
            )
            return
        rows = farm.sample(12)
        if not rows:
            await ev.reply(
                "🌾 <b>Pool is empty</b>\n\n"
                "<i>Nothing alive from the last scan. Run <code>/proxy scan</code> — "
                "free lists churn fast.</i>"
            )
            return
        await ev.reply(F.pool_card(rows, farm._db, farm.country, farm.stats()["total"]))
        return

    # ---------------------------------------------------- manual add / remove
    if sub in ("add", "del", "rm") and len(args) >= 2:
        farm = r.engine.farm or (r.engine.set_farm(True), r.engine.farm)[1]
        raw = args[1]
        if sub == "add":
            px = farm.add(raw)
            await ev.reply(
                f"✅ Added <code>{F.h(px or raw)}</code> to the pool."
                if px else
                "❌ That isn't a valid <code>ip:port</code> — nothing added."
            )
        else:
            ok = farm.remove(raw)
            await ev.reply("🗑 Dropped it from the pool." if ok else "❌ That one wasn't in the pool.")
        return

    # ---------------------------------------------------- status / check
    if not arg or arg.lower() in ("test", "check", "status"):
        px = r.cfg.proxy
        farm = r.engine.farm
        st = farm.stats() if farm else {}
        if farm:
            farm_line = (f"<b>{F.h(str(st.get('country', '')))}</b> · {st.get('total', 0)} IPs"
                         f" · {st.get('site_ok', 0)} site-tested")
        else:
            farm_line = "off"
        extra = ""
        if farm and st.get("total"):
            extra = (f"<i>{st.get('fresh', 0)} fresh · median "
                     f"{st.get('median_ms', 0)} ms</i>")
        if px:
            await _reply_proxy_check(ev, px)
            return
        await ev.reply(F.proxy_card(px, farm_line, extra))
        return

    if not arg.lower().startswith(("http://", "https://", "socks5://", "socks4://")):
        await ev.reply(
            "That doesn't look like a proxy.\n\n"
            "It has to start with <code>http://</code>, <code>https://</code> or "
            "<code>socks5://</code>."
        )
        return

    r.set_proxy(arg)
    await _reply_proxy_check(ev, arg, saved=True)


async def _whois(proxy: str = "") -> Dict:
    """Ask ip-api who we look like from a given exit (or from the box itself)."""
    kw: Dict = dict(timeout=httpx.Timeout(9, connect=6), verify=False)
    if proxy:
        kw["proxy"] = proxy
    async with httpx.AsyncClient(**kw) as c:
        r = await c.get(
            "http://ip-api.com/json/?fields=status,message,countryCode,regionName,city,"
            "isp,org,as,query"
        )
        d = r.json()
    if d.get("status") != "success":
        raise RuntimeError(d.get("message") or "lookup failed")
    return d


def _ip_block(label: str, d: Dict) -> Dict:
    from .proxyfarm import is_hosting, is_residential

    isp = (d.get("isp") or d.get("org") or "").strip()
    asn = (d.get("as") or "").split(" ")[0] if d.get("as") else ""
    hosting = is_hosting(isp) or is_hosting(d.get("as") or "")
    resi = (not hosting) and (is_residential(isp) or is_residential(d.get("as") or ""))
    if hosting:
        kind = "🏢 datacenter / hosting"
    elif resi:
        kind = "🏠 home or mobile line"
    else:
        kind = "❔ ISP I don't recognise"
    return {
        "label": label, "ip": d.get("query", "?"),
        "cc": (d.get("countryCode") or ""), "city": d.get("city") or "",
        "asn": asn, "isp": isp[:42] + ("…" if len(isp) > 42 else ""), "kind": kind,
        "hosting": hosting, "residential": resi,
    }


def _ip_verdict(b: Optional[Dict]) -> str:
    if not b or b.get("error"):
        return "Couldn't reach the lookup service — check the network and try again."
    if b.get("hosting"):
        return ("Datacenter ASN. The IP-gated sites will refuse this no matter which "
                "browser you use — run the bot on a home or mobile line, or point "
                "<code>PROXY</code> at a residential one.")
    if b.get("residential"):
        if b.get("cc") == "IN":
            return ("You're on an Indian home line, so vplink, gplinks and friends open "
                    "without any proxy. Keep the pool off with <code>/proxy off</code> — "
                    "your own IP is better than anything it can find.")
        return (f"Home line, but in {b.get('cc') or 'another country'}. Sites that check "
                "the country will still say no — <code>/proxy on IN</code> if you need "
                "an Indian exit.")
    return ("I can't place this ISP. Send a link and let the engine answer — if it hits "
            "the VPN wall, that's your verdict.")


async def cmd_ip(ev: Event) -> None:
    """Answer 'do I need a proxy here?' with evidence."""
    if not await _guard(ev):
        return
    cfg = ev.rt.cfg
    blocks: List[Dict] = []
    direct: Optional[Dict] = None

    try:
        direct = _ip_block("Direct — the machine running the bot", await _whois())
        blocks.append(direct)
    except Exception as exc:
        blocks.append({"label": "Direct — the machine running the bot",
                       "error": f"{type(exc).__name__}: {str(exc)[:110]}"})

    effective = direct
    if cfg.proxy:
        try:
            via = _ip_block("Via your fixed proxy", await _whois(cfg.proxy))
            blocks.append(via)
            effective = via
        except Exception as exc:
            blocks.append({"label": "Via your fixed proxy",
                           "error": f"{type(exc).__name__}: {str(exc)[:110]}"})

    await ev.reply(F.ip_card(blocks, _ip_verdict(effective)))


async def cmd_status(ev: Event) -> None:
    r = ev.rt
    up = int(time.time() - r.started)
    inflight, waiting = r.engine.load()
    st = r.db.stats()
    cached, hits = r.engine.cache_info()
    farm = ""
    if r.engine.farm is not None:
        fs = r.engine.farm.stats()
        farm = (f"{fs['country']} pool · {fs['total']} IPs · "
                f"{fs['site_ok']} site-tested · {fs['median_ms']} ms")
    browser = r.cfg.allow_browser and browser_available()
    await ev.reply(
        F.status_card(
            up, inflight, waiting, cached, hits,
            st["users"], st["active_today"], st["today"], st["ok_today"],
            browser, r.cfg.proxy, farm,
        )
    )


def browser_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except Exception:
        return False


@owner_only
async def cmd_stats(ev: Event) -> None:
    st = ev.rt.db.stats()
    await ev.reply(F.stats_card(st, st["top"]))


@owner_only
async def cmd_users(ev: Event) -> None:
    n = ev.rt.db.user_count()
    await ev.reply(f"👥 <b>{n}</b> users have talked to the bot so far.")


@owner_only
async def cmd_ban(ev: Event) -> None:
    r = ev.rt
    target = None
    if ev.reply_to_uid:
        target = ev.reply_to_uid
    elif ev.args and ev.args[0].lstrip("-").isdigit():
        target = int(ev.args[0])
    if not target:
        await ev.reply(
            "Reply to a message with <code>/ban</code>, or give me an ID: "
            "<code>/ban 123456</code>"
        )
        return
    r.db.ban(target, " ".join(ev.args[1:]) if len(ev.args) > 1 else "")
    await ev.reply(f"🚫 <code>{target}</code> can't use the bot anymore.")


@owner_only
async def cmd_unban(ev: Event) -> None:
    if not (ev.args and ev.args[0].lstrip("-").isdigit()):
        await ev.reply("Usage: <code>/unban 123456</code>")
        return
    target = int(ev.args[0])
    ev.rt.db.unban(target)
    await ev.reply(f"✅ <code>{target}</code> is welcome again.")


@owner_only
async def cmd_broadcast(ev: Event) -> None:
    text = ev.text.partition(" ")[2].strip()
    if not text:
        await ev.reply("Usage: <code>/broadcast your message</code>")
        return
    r = ev.rt
    ids = r.db.all_user_ids()
    await ev.reply(f"📤 Sending to {len(ids)} users…")
    ok = fail = 0
    for uid in ids:
        try:
            await ev.send(uid, text)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await ev.reply(f"✅ Delivered to {ok} · {fail} blocked or gone.")


# ------------------------------------------------------------------ resolve

async def _one(ev: Event, url: str) -> Dict:
    """Resolve a single URL and persist the outcome."""
    r = ev.rt
    try:
        data = await r.engine.resolve(url, allow_browser=r.allow_browser(ev.uid))
    except Exception as exc:
        data = {
            "input": url, "final": "", "ok": False, "method": "", "steps": [],
            "elapsed": 0.0, "note": f"{type(exc).__name__}: {exc}", "blocked": "",
        }
    r.db.bump_user(ev.uid)
    r.db.log_request(
        ev.uid, sites.host_of(url), url, data.get("final", ""),
        bool(data.get("ok")), data.get("method", ""), float(data.get("elapsed", 0)),
    )
    return data


def _buttons(data: Dict, key: str):
    """Every result gets a way forward: the path, the link, or a second attempt."""
    row = [("🧭 Path", "cb:" + key)]
    if data.get("ok") and data.get("final"):
        row.append(("🔗 Open", "url:" + data["final"]))
    elif data.get("method") != "browser" and (data.get("blocked") or "") != "vpn":
        row.append(("🌐 Try browser", "cb:rb:" + key))
    return [row]


async def do_bypass(ev: Event, urls: List[str]) -> None:
    r = ev.rt

    allowed, retry = r.allow(ev.uid)
    if not allowed:
        await ev.reply(F.rate_limited(retry))
        return

    # file hosts: no content scraping
    todo: List[str] = []
    for url in urls:
        scraper = sites.is_scraper_only(url)
        if scraper:
            await ev.reply(
                f"🚫 <b>Not this one</b>\n"
                f"<code>{F.h(F.short(url, 60))}</code>\n\n"
                f"<i>{F.h(scraper)} is a file host. Pulling direct download links out "
                f"of it means scraping pirated content, and that's not something "
                f"this bot does.</i>"
            )
        else:
            todo.append(url)
    if not todo:
        return

    total = len(todo)
    status = await ev.reply(F.resolving(total))
    await ev.typing()

    # parallel resolve: multiple links don't wait for each other
    results = await asyncio.gather(*[_one(ev, u) for u in todo])

    for i, data in enumerate(results, 1):
        text = F.result_card(data, i, total)
        key = r.put_trace(data)
        kb = _buttons(data, key)

        if i == 1:
            try:
                await status.edit(text, kb)
                continue
            except Exception:
                pass
        await ev.reply(text, kb)


async def cmd_bypass(ev: Event) -> None:
    if not await _guard(ev):
        return
    text = ev.text or ""
    urls = extract_urls(text.partition(" ")[2] if " " in text else "", ev.rt.cfg.max_urls)
    if not urls:
        await ev.reply(
            "Give me something to work with:\n\n"
            "<code>/bypass https://gplinks.co/abc123</code>"
        )
        return
    await do_bypass(ev, urls)


async def on_text(ev: Event) -> None:
    if not await _guard(ev):
        return
    urls = extract_urls(ev.text or "", ev.rt.cfg.max_urls)
    if not urls:
        return  # don't interrupt normal chat
    await do_bypass(ev, urls)


async def on_callback(ev: Event) -> None:
    r = ev.rt
    payload = ev.callback_data or ""

    # ---------------------------------------------------------- browser retry
    if payload.startswith("rb:"):
        data = r.get_trace(payload[3:])
        if not data:
            await ev.answer("That card expired — send the link again.", alert=True)
            return
        url = data.get("input") or ""
        await ev.answer("Opening a browser — give it a few seconds…")
        try:
            await ev.clear_buttons()
            await ev.typing()
        except Exception:
            pass
        try:
            new = await r.engine.resolve(url, allow_browser=True, fresh=True)
        except Exception as exc:
            await ev.reply(
                f"⚠️ <b>The browser run fell over</b>\n\n<i>{F.h(str(exc)[:180])}</i>"
            )
            return
        key = r.put_trace(new)
        try:
            await ev.edit_self(F.result_card(new), _buttons(new, key))
        except Exception:
            await ev.reply(F.result_card(new), _buttons(new, key))
        return

    # ---------------------------------------------------------- trace
    data = r.get_trace(payload)
    if not data:
        await ev.answer("This trace expired — resolve the link again.", alert=True)
        return
    await ev.answer()
    try:
        await ev.reply(F.trace_card(data))
    except Exception:
        pass


async def report_error(exc: BaseException, send_to: Optional[int] = None) -> None:
    """Log an unhandled error, and tell the owner's log chat if there is one."""
    print(f"[error] {type(exc).__name__}: {exc}")
