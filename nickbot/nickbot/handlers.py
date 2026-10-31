"""Command & message handlers."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import OrderedDict, deque
from typing import Deque, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

import httpx

from . import format as F
from . import sites
from .config import Config, set_env_value
from .db import DB
from .resolver import Engine

URL_RE = re.compile(r"https?://[^\s<>\"'）)\]]+", re.I)
BARE_RE = re.compile(r"(?<![\w@.])((?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s<>\"'）)\]]*)", re.I)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

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


def rt(context: ContextTypes.DEFAULT_TYPE) -> Runtime:
    return context.application.bot_data["rt"]


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    u = update.effective_user
    if not u:
        return False
    r = rt(context)
    if r.db.is_banned(u.id):
        return False
    r.db.touch_user(u.id, u.username or "", u.first_name or "")
    return True


def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if not u or not rt(context).cfg.is_owner(u.id):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "🔒 That one's for the owner only.\n"
                    "<i>Send a link instead — I'll take care of it.</i>",
                    parse_mode=ParseMode.HTML,
                )
            return
        return await func(update, context)

    wrapper.__name__ = func.__name__
    return wrapper


# ------------------------------------------------------------------ commands

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    u = update.effective_user
    r = rt(context)
    await update.effective_message.reply_text(
        F.help_text(r.cfg.is_owner(u.id)),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    c = update.effective_chat
    await update.effective_message.reply_text(
        f"👤 You: <code>{u.id}</code>\n💬 This chat: <code>{c.id}</code>\n\n"
        "<i>Handy if you're setting up the logger chat or adding yourself as owner.</i>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    r = rt(context)
    uid = update.effective_user.id
    arg = (context.args[0].lower() if context.args else "").strip()
    if arg in ("fast", "http", "快速"):
        r.set_mode(uid, "fast")
        msg = ("⚡ <b>Fast mode</b>\n"
               "HTTP only. Quick, but a gate that runs a countdown will beat you.")
    elif arg in ("browser", "slow", "浏览器"):
        r.set_mode(uid, "browser")
        msg = ("🌐 <b>Browser mode</b>\n"
               "A real Chromium opens the page. Slower, but it clicks through "
               "countdowns and popups that HTTP can't touch.")
    elif arg in ("auto", "自动", ""):
        r.set_mode(uid, "auto")
        msg = ("<b>Auto</b> it is.\n"
               "Fast path first, browser only if the gate actually needs one. "
               "This is what you want.")
    else:
        msg = ("Usage: <code>/mode auto</code> · <code>/mode fast</code> · "
               "<code>/mode browser</code>")
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_supported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/sites` 看全表，`/sites gplinks` 查一个。"""
    q = " ".join(context.args or []).strip()
    text = sites.search_text(q) if q else sites.supported_text()
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


async def _farm_loop(rt_obj: Runtime) -> None:
    """后台保温，但按需且温和。

    以前一启动就扫 220 个代理，几百条 SYN 把出站连接占满，Telegram 的长轮询
    直接超时（手机上尤其明显）。现在只有真的被 IP 墙挡过才补货，而且隔很久才动一次。
    """
    await asyncio.sleep(300)          # 启动后先让轮询站稳
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


async def _reply_proxy_check(msg, px: str, saved: bool = False) -> None:
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
    await msg.reply_text(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


@owner_only
async def cmd_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = rt(context)
    msg = update.effective_message
    args = list(context.args or [])
    arg = " ".join(args).strip()
    sub = (args[0].lower() if args else "")

    # ---------------------------------------------------- 固定代理
    if arg.lower() in ("none", "clear", "reset"):
        r.set_proxy("")
        await msg.reply_text(
            "🛰 <b>Back to the server's own IP</b>\n\n"
            "<i>No custom exit anymore. If a gate starts refusing us again, "
            "<code>/proxy on IN</code> is the way back.</i>",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # ---------------------------------------------------- 农场开关
    if sub in ("on", "off") and len(args) <= 2:
        if sub == "off":
            r.engine.set_farm(False)
            set_env_value("PROXY_FARM", "false")
            await msg.reply_text(
                "🌾 <b>Pool off</b>\n\n"
                "<i>No more automatic IP rotation. Links that need a local IP will "
                "fail honestly instead of retrying.</i>",
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        cc = (args[1].upper()[:2] if len(args) > 1 else r.cfg.proxy_country)
        r.engine.set_farm(True, cc)
        set_env_value("PROXY_FARM", "true")
        set_env_value("PROXY_COUNTRY", cc)
        st = r.engine.farm.stats() if r.engine.farm else {}
        await msg.reply_text(
            f"🌾 <b>Pool on · {F.h(cc)}</b>\n\n"
            f"{st.get('total', 0)} IPs in hand, {st.get('site_ok', 0)} already cleared "
            "a real gate.\n\n"
            "<i>It only kicks in when a site walls our IP — normal links don't pay "
            "for it.</i>\n"
            "<code>/proxy scan</code> fills the pool right now.",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # ---------------------------------------------------- 扫描补货
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
        note = await msg.reply_text(
            f"🌾 <b>Looking for {F.h(cc)} proxies…</b>\n"
            "<i>harvest → ask each one for its exit IP → check the country"
            f"{' → open the site through it' if target else ''}</i>",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        rep = await farm.refresh(target=target or None, limit=250, budget=55)
        st = farm.stats()
        await note.edit_text(
            F.scan_card(cc, rep, st, target),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # ---------------------------------------------------- 列表
    if sub in ("list", "ls", "show"):
        farm = r.engine.farm
        if not farm:
            await msg.reply_text(
                "🌾 <b>Pool is off</b>\n\nTurn it on with <code>/proxy on IN</code> first.",
                parse_mode=ParseMode.HTML,
            )
            return
        rows = farm.sample(12)
        if not rows:
            await msg.reply_text(
                "🌾 <b>Pool is empty</b>\n\n"
                "<i>Nothing alive from the last scan. Run <code>/proxy scan</code> — "
                "free lists churn fast.</i>",
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        await msg.reply_text(
            F.pool_card(rows, farm._db, farm.country, farm.stats()["total"]),
            parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW,
        )
        return

    # ---------------------------------------------------- 手动增删
    if sub in ("add", "del", "rm") and len(args) >= 2:
        farm = r.engine.farm or (r.engine.set_farm(True), r.engine.farm)[1]
        raw = args[1]
        if sub == "add":
            px = farm.add(raw)
            await msg.reply_text(
                f"✅ Added <code>{F.h(px or raw)}</code> to the pool."
                if px else
                "❌ That isn't a valid <code>ip:port</code> — nothing added.",
                parse_mode=ParseMode.HTML)
        else:
            ok = farm.remove(raw)
            await msg.reply_text(
                "🗑 Dropped it from the pool." if ok else "❌ That one wasn't in the pool.",
                parse_mode=ParseMode.HTML)
        return

    # ---------------------------------------------------- 状态 / 检测
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
            await _reply_proxy_check(msg, px)
            return
        await msg.reply_text(
            F.proxy_card(px, farm_line, extra),
            parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW,
        )
        return

    if not arg.lower().startswith(("http://", "https://", "socks5://", "socks4://")):
        await msg.reply_text(
            "That doesn't look like a proxy.\n\n"
            "It has to start with <code>http://</code>, <code>https://</code> or "
            "<code>socks5://</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    r.set_proxy(arg)
    await _reply_proxy_check(msg, arg, saved=True)


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


async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer 'do I need a proxy here?' with evidence."""
    if not await _guard(update, context):
        return
    r = rt(context)
    cfg = r.cfg
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

    await update.effective_message.reply_text(
        F.ip_card(blocks, _ip_verdict(effective)),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = rt(context)
    up = int(time.time() - r.started)
    inflight, waiting = r.engine.load()
    st = r.db.stats()
    cached, hits = r.engine.cache_info()
    farm = ""
    if r.engine.farm is not None:
        fs = r.engine.farm.stats()
        farm = (f"{fs['country']} pool · {fs['total']} IPs · "
                f"{fs['site_ok']} site-tested · {fs['median_ms']} ms")
    await update.effective_message.reply_text(
        F.status_card(
            up, inflight, waiting, cached, hits,
            st["users"], st["active_today"], st["today"], st["ok_today"],
            r.cfg.allow_browser, r.cfg.proxy, farm,
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@owner_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = rt(context).db.stats()
    await update.effective_message.reply_text(
        F.stats_card(st, st["top"]),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@owner_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    n = rt(context).db.user_count()
    await update.effective_message.reply_text(
        f"👥 <b>{n}</b> users have talked to the bot so far.", parse_mode=ParseMode.HTML
    )


@owner_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = rt(context)
    target = None
    if update.effective_message.reply_to_message:
        target = update.effective_message.reply_to_message.from_user.id
    elif context.args and context.args[0].lstrip("-").isdigit():
        target = int(context.args[0])
    if not target:
        await update.effective_message.reply_text(
            "Reply to a message with <code>/ban</code>, or give me an ID: "
            "<code>/ban 123456</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    r.db.ban(target, " ".join(context.args[1:]) if len(context.args) > 1 else "")
    await update.effective_message.reply_text(
        f"🚫 <code>{target}</code> can't use the bot anymore.", parse_mode=ParseMode.HTML
    )


@owner_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not (context.args and context.args[0].lstrip("-").isdigit()):
        await update.effective_message.reply_text(
            "Usage: <code>/unban 123456</code>", parse_mode=ParseMode.HTML
        )
        return
    target = int(context.args[0])
    rt(context).db.unban(target)
    await update.effective_message.reply_text(
        f"✅ <code>{target}</code> is welcome again.", parse_mode=ParseMode.HTML
    )


@owner_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text.partition(" ")[2].strip()
    if not text:
        await update.effective_message.reply_text(
            "Usage: <code>/broadcast your message</code>", parse_mode=ParseMode.HTML
        )
        return
    r = rt(context)
    ids = r.db.all_user_ids()
    await update.effective_message.reply_text(f"📤 Sending to {len(ids)} users…")
    ok = fail = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await update.effective_message.reply_text(
        f"✅ Delivered to {ok} · {fail} blocked or gone."
    )


# ------------------------------------------------------------------ resolve

async def _one(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> Dict:
    """Resolve a single URL and persist the outcome."""
    r = rt(context)
    uid = update.effective_user.id
    try:
        data = await r.engine.resolve(url, allow_browser=r.allow_browser(uid))
    except Exception as exc:
        data = {
            "input": url, "final": "", "ok": False, "method": "", "steps": [],
            "elapsed": 0.0, "note": f"{type(exc).__name__}: {exc}", "blocked": "",
        }
    r.db.bump_user(uid)
    r.db.log_request(
        uid, sites.host_of(url), url, data.get("final", ""),
        bool(data.get("ok")), data.get("method", ""), float(data.get("elapsed", 0)),
    )
    return data


def _buttons(data: Dict, key: str) -> InlineKeyboardMarkup:
    """Every result gets a way forward: the path, the link, or a second attempt."""
    row = [InlineKeyboardButton("🧭 Path", callback_data=key)]
    if data.get("ok") and data.get("final"):
        row.append(InlineKeyboardButton("🔗 Open", url=data["final"]))
    elif data.get("method") != "browser" and (data.get("blocked") or "") != "vpn":
        row.append(InlineKeyboardButton("🌐 Try browser", callback_data="rb:" + key))
    return InlineKeyboardMarkup([row])


async def do_bypass(update: Update, context: ContextTypes.DEFAULT_TYPE, urls: List[str]) -> None:
    r = rt(context)
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    allowed, retry = r.allow(uid)
    if not allowed:
        await update.effective_message.reply_text(
            F.rate_limited(retry), parse_mode=ParseMode.HTML
        )
        return

    # file hosts: no content scraping
    todo: List[str] = []
    for url in urls:
        scraper = sites.is_scraper_only(url)
        if scraper:
            await update.effective_message.reply_text(
                f"🚫 <b>Not this one</b>\n"
                f"<code>{F.h(F.short(url, 60))}</code>\n\n"
                f"<i>{F.h(scraper)} is a file host. Pulling direct download links out "
                f"of it means scraping pirated content, and that's not something "
                f"this bot does.</i>",
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
        else:
            todo.append(url)
    if not todo:
        return

    total = len(todo)
    status = await update.effective_message.reply_text(
        F.resolving(total),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception:
        pass

    # parallel resolve: multiple links don't wait for each other
    results = await asyncio.gather(*[_one(update, context, u) for u in todo])

    for i, data in enumerate(results, 1):
        text = F.result_card(data, i, total)
        key = r.put_trace(data)
        kb = _buttons(data, key)

        if i == 1:
            try:
                await status.edit_text(
                    text, parse_mode=ParseMode.HTML, reply_markup=kb, link_preview_options=NO_PREVIEW
                )
                continue
            except Exception:
                pass
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, link_preview_options=NO_PREVIEW
        )


async def cmd_bypass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    text = update.effective_message.text or ""
    urls = extract_urls(text.partition(" ")[2] if " " in text else "", rt(context).cfg.max_urls)
    if not urls:
        await update.effective_message.reply_text(
            "Give me something to work with:\n\n"
            "<code>/bypass https://gplinks.co/abc123</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await do_bypass(update, context, urls)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    text = update.effective_message.text or ""
    urls = extract_urls(text, rt(context).cfg.max_urls)
    if not urls:
        return  # don't interrupt normal chat
    await do_bypass(update, context, urls)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    r = rt(context)
    payload = q.data or ""

    # ---------------------------------------------------------- browser retry
    if payload.startswith("rb:"):
        data = r.get_trace(payload[3:])
        if not data:
            await q.answer("That card expired — send the link again.", show_alert=True)
            return
        url = data.get("input") or ""
        await q.answer("Opening a browser — give it a few seconds…")
        try:
            await q.message.edit_reply_markup(reply_markup=None)
            await context.bot.send_chat_action(q.message.chat_id, ChatAction.TYPING)
        except Exception:
            pass
        try:
            new = await r.engine.resolve(url, allow_browser=True, fresh=True)
        except Exception as exc:
            await q.message.reply_text(
                f"⚠️ <b>The browser run fell over</b>\n\n<i>{F.h(str(exc)[:180])}</i>",
                parse_mode=ParseMode.HTML,
            )
            return
        key = r.put_trace(new)
        try:
            await q.message.edit_text(
                F.result_card(new), parse_mode=ParseMode.HTML,
                reply_markup=_buttons(new, key), link_preview_options=NO_PREVIEW,
            )
        except Exception:
            await q.message.reply_text(
                F.result_card(new), parse_mode=ParseMode.HTML,
                reply_markup=_buttons(new, key), link_preview_options=NO_PREVIEW,
            )
        return

    # ---------------------------------------------------------- trace
    data = r.get_trace(payload)
    if not data:
        await q.answer("This trace expired — resolve the link again.", show_alert=True)
        return
    await q.answer()
    try:
        await q.message.reply_text(
            F.trace_card(data), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
        )
    except Exception:
        pass


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    app = context.application
    r: Optional[Runtime] = app.bot_data.get("rt")
    if r and r.cfg.logger_chat:
        try:
            await app.bot.send_message(
                r.cfg.logger_chat,
                f"⚠️ <b>Error</b>\n<code>{F.h(type(err).__name__)}</code>\n{F.h(str(err))[:600]}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    print(f"[error] {type(err).__name__}: {err}")
