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
                await update.effective_message.reply_text("⛔ Owner-only command.")
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
        f"👤 Your ID: <code>{u.id}</code>\n💬 This chat ID: <code>{c.id}</code>",
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
        msg = "Switched to ⚡ <b>Fast mode</b>: HTTP only, no browser. JS-only gates will fail."
    elif arg in ("browser", "slow", "浏览器"):
        r.set_mode(uid, "browser")
        msg = "Switched to 🌐 <b>Browser mode</b>: slower, but can pass countdowns and popups."
    elif arg in ("auto", "自动", ""):
        r.set_mode(uid, "auto")
        msg = "Switched to <b>Auto</b>: fast when possible, browser only when needed."
    else:
        msg = "Usage: <code>/mode auto|fast|browser</code>"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_supported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        sites.supported_text(), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


async def _farm_loop(rt_obj: Runtime) -> None:
    """后台保温：池子空了或整体过期就自动补货，用户永远不用手动 scan。"""
    await asyncio.sleep(5)
    while True:
        farm = rt_obj.engine.farm
        if farm is not None:
            st = farm.stats()
            stale = st["fresh"] < max(3, st["total"] // 3)
            if st["total"] < 8 or stale:
                try:
                    await farm.refresh(limit=220, budget=50)
                except Exception:
                    pass
        await asyncio.sleep(1200)


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
            "⚠️ <b>Proxy saved but unreachable</b>\n"
            f"<code>{F.h(px)}</code>\n"
            f"<i>{F.h(err)}</i>\n\n"
            "Check the host:port / user:pass, or <code>/proxy off</code>."
        )
    else:
        text = (
            f"✅ <b>Proxy {'saved' if saved else 'works'}</b>\n"
            f"<code>{F.h(px)}</code>\n"
            f"Exit IP <code>{F.h(ip)}</code>{(' · ' + F.h(country)) if country else ''} · {took:.1f}s\n\n"
            "Retry the link — IP-walled gates should pass now."
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
            "🛰 <b>Fixed proxy cleared</b> — back to the server's own IP.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ---------------------------------------------------- 农场开关
    if sub in ("on", "off") and len(args) <= 2:
        if sub == "off":
            r.engine.set_farm(False)
            set_env_value("PROXY_FARM", "false")
            await msg.reply_text(
                "🌾 <b>Proxy farm off</b> — no automatic IP rotation.",
                parse_mode=ParseMode.HTML,
            )
            return
        cc = (args[1].upper()[:2] if len(args) > 1 else r.cfg.proxy_country)
        r.engine.set_farm(True, cc)
        set_env_value("PROXY_FARM", "true")
        set_env_value("PROXY_COUNTRY", cc)
        st = r.engine.farm.stats() if r.engine.farm else {}
        await msg.reply_text(
            f"🌾 <b>Proxy farm on · {F.h(cc)}</b>\n"
            f"Pool: {st.get('total', 0)} IPs ({st.get('site_ok', 0)} verified on sites)\n\n"
            "It only kicks in when a site walls our IP, and it costs nothing when it doesn't.\n"
            f"Run <code>/proxy scan</code> to fill the pool now.",
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
            f"🌾 Scanning for <b>{F.h(cc)}</b> proxies…\n"
            "<i>harvest → exit-IP check → country verify"
            f"{' → site test' if target else ''}</i>",
            parse_mode=ParseMode.HTML,
        )
        rep = await farm.refresh(target=target or None, limit=250, budget=55)
        st = farm.stats()
        lines = [
            f"🌾 <b>{F.h(cc)} proxy farm</b>",
            f"Candidates {rep.get('candidates', 0)} · reached {rep.get('reached', 0)}"
            f" · verified {rep.get('verified', 0)}",
            f"Kept <b>{st['total']}</b> · residential {st.get('residential', 0)}"
            f" · median {st['median_ms']} ms"
            + (f" · site-ok {rep.get('site_ok', 0)}" if target else ""),
            f"⏱ {rep.get('seconds', 0)}s",
        ]
        if st["total"] == 0:
            lines += ["", "<i>Nothing alive right now. Free lists churn fast — scan again in a few minutes.</i>"]
        elif st.get("residential", 0) == 0:
            lines += ["", "<i>All survivors are hosting ASNs — gates that check ISP will still 403 them.</i>"]
        await note.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    # ---------------------------------------------------- 列表
    if sub in ("list", "ls", "show"):
        farm = r.engine.farm
        if not farm:
            await msg.reply_text("🌾 Farm is off. <code>/proxy on</code> first.",
                                 parse_mode=ParseMode.HTML)
            return
        rows = farm.sample(12)
        if not rows:
            await msg.reply_text("🌾 Pool is empty. Run <code>/proxy scan</code>.",
                                 parse_mode=ParseMode.HTML)
            return
        body = "\n".join(
            f"{i}. <code>{F.h(p.split('://')[-1])}</code> · {farm._db.get(p, {}).get('latency', '?')} ms"
            f"{' · 🏠' if farm._db.get(p, {}).get('residential') else ''}"
            f"{' · ✅' if farm._db.get(p, {}).get('site') else ''}"
            f" · {F.h(str(farm._db.get(p, {}).get('city') or '?'))}"
            for i, p in enumerate(rows, 1)
        )
        await msg.reply_text(
            f"🌾 <b>{farm.country} pool</b> ({farm.stats()['total']} total)\n{body}",
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
                f"✅ Added <code>{F.h(px or raw)}</code>" if px else "❌ Not a valid ip:port",
                parse_mode=ParseMode.HTML)
        else:
            ok = farm.remove(raw)
            await msg.reply_text("🗑 Removed." if ok else "❌ Not in pool.",
                                 parse_mode=ParseMode.HTML)
        return

    # ---------------------------------------------------- 状态 / 检测
    if not arg or arg.lower() in ("test", "check", "status"):
        px = r.cfg.proxy
        farm = r.engine.farm
        st = farm.stats() if farm else {}
        if farm:
            farm_line = f"<b>{F.h(str(st.get('country', '')))}</b> · {st.get('total', 0)} IPs"
        else:
            farm_line = "off"
        head = (
            "🛰 <b>Proxy</b>\n"
            f"Fixed: <code>{F.h(px.split('@')[-1]) if px else 'none'}</code>\n"
            f"Farm: {farm_line}"
        )
        if farm and st.get("total"):
            head += (f"\nFresh {st.get('fresh', 0)} · site-ok {st.get('site_ok', 0)}"
                     f" · median {st.get('median_ms', 0)} ms")
        head += (
            "\n\n<b>Commands</b>\n"
            "<code>/proxy on [IN]</code> — build/rotate a country pool\n"
            "<code>/proxy scan [IN] [url]</code> — refill + test\n"
            "<code>/proxy list</code> · <code>/proxy add ip:port</code>\n"
            "<code>/proxy http://user:pass@host:port</code> — fixed proxy\n"
            "<code>/proxy off</code> — disable rotation"
        )
        if px:
            await _reply_proxy_check(msg, px)
            return
        await msg.reply_text(head, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        return

    if not arg.lower().startswith(("http://", "https://", "socks5://", "socks4://")):
        await msg.reply_text(
            "Proxy must start with <code>http://</code> or <code>socks5://</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    r.set_proxy(arg)
    await _reply_proxy_check(msg, arg, saved=True)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = rt(context)
    up = int(time.time() - r.started)
    inflight, waiting = r.engine.load()
    st = r.db.stats()
    cached, hits = r.engine.cache_info()
    text = (
        "<b>Status</b>\n"
        f"⏱ Uptime {up // 3600}h {up % 3600 // 60}m\n"
        f"⚙️ In flight {inflight} · queued {waiting}\n"
        f"🗄 Cache {cached} entries · {hits} hits\n"
        f"👥 Users {st['users']} · active 24h {st['active_today']}\n"
        f"📊 Requests 24h {st['today']} (ok {st['ok_today']})\n"
        f"🌐 Browser fallback: {'on' if r.cfg.allow_browser else 'off'}\n"
        f"🛰 Proxy: {(r.cfg.proxy.split('@')[-1] if r.cfg.proxy else 'none')}"
        f"{' · auto-failover on' if r.cfg.auto_proxy else ''}"
    )
    if r.engine.farm is not None:
        fs = r.engine.farm.stats()
        text += (f"\n🌾 Farm {fs['country']}: {fs['total']} IPs"
                 f" · site-ok {fs['site_ok']} · {fs['median_ms']} ms")
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = rt(context)
    st = r.db.stats()
    top = "\n".join(f"　{i}. {d} — {n}" for i, (d, n) in enumerate(st["top"], 1)) or "　(none yet)"
    text = (
        "<b>Stats</b>\n"
        f"👥 Users {st['users']} · active 24h {st['active_today']}\n"
        f"📨 Requests total {st['total']} · 24h {st['today']}\n"
        f"✅ Success rate 24h {st['success_rate']:.1f}%\n"
        f"⏱ Avg time 24h {st['avg_elapsed']:.1f}s\n\n"
        f"<b>Top sites (24h)</b>\n{top}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@owner_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    n = rt(context).db.user_count()
    await update.effective_message.reply_text(f"👥 Total users: <b>{n}</b>", parse_mode=ParseMode.HTML)


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
            "Usage: reply to a message with /ban, or <code>/ban 123456</code>", parse_mode=ParseMode.HTML
        )
        return
    r.db.ban(target, " ".join(context.args[1:]) if len(context.args) > 1 else "")
    await update.effective_message.reply_text(f"🚫 Banned <code>{target}</code>", parse_mode=ParseMode.HTML)


@owner_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not (context.args and context.args[0].lstrip("-").isdigit()):
        await update.effective_message.reply_text("Usage: <code>/unban 123456</code>", parse_mode=ParseMode.HTML)
        return
    target = int(context.args[0])
    rt(context).db.unban(target)
    await update.effective_message.reply_text(f"✅ Unbanned <code>{target}</code>", parse_mode=ParseMode.HTML)


@owner_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text.partition(" ")[2].strip()
    if not text:
        await update.effective_message.reply_text("Usage: <code>/broadcast text</code>", parse_mode=ParseMode.HTML)
        return
    r = rt(context)
    ids = r.db.all_user_ids()
    await update.effective_message.reply_text(f"📤 Broadcasting to {len(ids)} users…")
    ok = fail = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await update.effective_message.reply_text(f"✅ Done: {ok} sent · {fail} failed")


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


async def do_bypass(update: Update, context: ContextTypes.DEFAULT_TYPE, urls: List[str]) -> None:
    r = rt(context)
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    allowed, retry = r.allow(uid)
    if not allowed:
        await update.effective_message.reply_text(
            f"🐢 Slow down — try again in {retry}s.", parse_mode=ParseMode.HTML
        )
        return

    # file hosts: no content scraping
    todo: List[str] = []
    for url in urls:
        scraper = sites.is_scraper_only(url)
        if scraper:
            await update.effective_message.reply_text(
                f"🚫 <code>{F.h(F.short(url, 60))}</code>\n"
                f"File host ({F.h(scraper)}) — extracting direct links means scraping pirated "
                f"content, which this bot does not do.",
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
        else:
            todo.append(url)
    if not todo:
        return

    total = len(todo)
    status = await update.effective_message.reply_text(
        f"⏳ <b>Resolving {total} link{'s' if total > 1 else ''}…</b>",
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
        row = [InlineKeyboardButton("🧭 Trace", callback_data=key)]
        if data.get("ok") and data.get("final"):
            row.append(InlineKeyboardButton("🔗 Open", url=data["final"]))
        kb = InlineKeyboardMarkup([row])

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
            "Usage: <code>/bypass https://example.com/xxxx</code>", parse_mode=ParseMode.HTML
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
    data = rt(context).get_trace(q.data or "")
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
