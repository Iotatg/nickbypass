"""Message copy.

Design rules for everything in this file:

* facts first, decoration second — a user should read the destination in one glance
* talk like a person, not a log line ("You sent" / "What happened", not "INPUT/PARAM")
* never lie: if something failed, say what failed and what to do next
* every user-supplied string goes through ``h()`` — no exceptions
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import sites

MAX_LEN = 3800
RULE = "─────────────────"
FOOTER = f"{RULE}\n⚡ @nova93ec2c4_bot"


# --------------------------------------------------------------------- helpers


def h(s: Any) -> str:
    """Escape for Telegram HTML."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def short(url: str, n: int = 72) -> str:
    url = url or ""
    return url if len(url) <= n else url[: n - 1] + "…"


def _seconds(sec: float) -> str:
    if sec <= 0:
        return "0s"
    if sec < 1:
        return f"{sec:.2f}s"
    if sec < 60:
        return f"{sec:.1f}s"
    return f"{int(sec) // 60}m {int(sec) % 60}s"


def _hops(n: int) -> str:
    if n <= 0:
        return "no hops"
    return "1 hop" if n == 1 else f"{n} hops"


_METHOD = {
    "http": "direct HTTP",
    "gate": "site API",
    "browser": "headless browser",
    "rules": "known pattern",
}


def _method(method: str) -> str:
    return _METHOD.get(method or "", "unknown path")


def _meta(data: Dict[str, Any]) -> str:
    """The one quiet line under every card: time · how · how far."""
    bits: List[str] = []
    if data.get("cached"):
        bits.append("instant · from cache")
    else:
        bits.append(_seconds(float(data.get("elapsed") or 0.0)))
    bits.append(_method(data.get("method", "")))
    hops = len(data.get("steps") or [])
    if hops:
        bits.append(_hops(hops))
    if "rotating proxy" in (data.get("note") or ""):
        bits.append("local exit IP")
    return " · ".join(bits)


def _block(label: str, value: str, limit: int = 400) -> List[str]:
    return [f"<b>{label}</b>", f"<code>{h(short(value, limit))}</code>", ""]


# ---------------------------------------------------------------- result cards


def result_card(data: Dict[str, Any], idx: int = 1, total: int = 1) -> str:
    """One resolution result -> the message the user actually reads."""
    url = data.get("input", "")
    site = sites.display_name(url)
    ok = bool(data.get("ok"))
    final = data.get("final", "")
    note = data.get("note", "")

    where = f"<i>{h(site)}</i>"
    if total > 1:
        where = f"{idx}/{total} · " + where

    if ok and final:
        lines = [f"✅ <b>Link bypassed</b> · {where}", ""]
        lines += _block("You sent", url, 200)
        lines += _block("Real destination", final, 400)
        lines.append(f"<i>{_closer(data)}</i>")
        lines.append("")
        lines.append(f"<i>{h(_meta(data))}</i>")
        lines += ["", FOOTER]
        return "\n".join(lines)

    lines = [f"⚠️ <b>Couldn't get through</b> · {where}", ""]
    lines += _block("You sent", url, 200)
    if final and final != url:
        lines += _block("Stopped at", final, 200)

    what, fix, known = _explain(data)
    lines += ["<b>What happened</b>", h(what), ""]
    lines += ["<b>What to try</b>", fix, ""]
    if note and not known and not _note_is_noise(note):
        lines += [f"<i>{h(short(note, 160))}</i>", ""]
    lines.append(f"<i>{h(_meta(data))}</i>")
    lines += ["", FOOTER]
    return "\n".join(lines)


def _closer(data: Dict[str, Any]) -> str:
    """A human sign-off line, matched to how much work the chain took."""
    hops = len(data.get("steps") or [])
    if "rotating proxy" in (data.get("note") or ""):
        return "Got past the IP check with a local exit — this is the real page."
    if hops <= 1:
        return "One hop and done — nothing else in the way."
    if hops <= 4:
        return "Straight through the gate — that's the real destination."
    return "Long chain, but this is the end of it."


def _note_is_noise(note: str) -> bool:
    """Raw engine notes that only repeat what the card already says."""
    low = note.lower()
    return any(
        k in low
        for k in ("reached destination", "no further redirect", "browser run finished",
                  "resolved with browser")
    )


def _explain(data: Dict[str, Any]) -> tuple[str, str, bool]:
    """(what happened, what to try, did we recognise the cause).

    The third value decides whether the raw engine note is still worth showing —
    repeating "403 from Cloudflare" under an explanation of Cloudflare is noise.
    """
    low = (data.get("note") or "").lower()
    blocked = (data.get("blocked") or "").lower()

    if blocked == "vpn":
        return (
            "This site checks the IP the request comes from and turns down datacenter "
            "and VPN addresses. Nothing on our side can disguise that.",
            "<code>/proxy on IN</code> builds a pool of local IPs. A residential proxy in "
            "PROXY works too, if you have one.",
            True,
        )
    if "disabled" in low and "browser" in low:
        return (
            "This gate needs a real browser, but you're in fast mode.",
            "<code>/mode browser</code> — or just tap the button under this card.",
            True,
        )
    if any(k in low for k in ("captcha", "challenge", "cloudflare", "just a moment", "anti-bot")):
        return (
            "An anti-bot wall closed the gate before it opened — usually Cloudflare "
            "waiting for a JavaScript check.",
            "<code>/mode browser</code> — a real browser clears that check.",
            True,
        )
    if "invalid or expired" in low:
        return (
            "The shortener's own page says this link is dead — expired, or removed "
            "by whoever created it.",
            "Ask for a fresh link. There's nothing to unwrap here: the destination "
            "was deleted, not hidden.",
            True,
        )
    if "still on the shortener" in low:
        return (
            "The page never produced a destination — this shortener builds it with "
            "JavaScript after the countdown.",
            "<code>/mode browser</code> — a real browser lets that script finish.",
            True,
        )
    if any(k in low for k in ("timeout", "timed out", "connect", "read error", "network")):
        return (
            "The site stopped answering partway down the chain.",
            "Resend in a minute. <code>/mode browser</code> if it keeps happening.",
            True,
        )
    if any(k in low for k in ("http 4", "http 5", "404", "403", "410", "dead")):
        return (
            "The chain ended on an error page, so the link itself may be dead.",
            "Open it once in your own browser. If it loads there, send it again.",
            True,
        )
    if "browser" in low:
        return (
            "This gate wants a countdown, a script and a button press — HTTP alone "
            "can't do that.",
            "<code>/mode browser</code>, then paste the link again.",
            True,
        )
    if any(k in low for k in ("no destination", "ended", "loop")):
        return (
            "The chain stopped on an ad page that never points anywhere real.",
            "Retry in a few minutes — these chains rotate their targets.",
            True,
        )
    return (
        "The chain didn't end on anything that looks like a real destination.",
        "Retry in a moment. <code>/mode browser</code> if you want to force it.",
        False,
    )


# ---------------------------------------------------------------- trace card

KIND_LABEL = {
    "request": "GET",
    "redirect": "REDIRECT",
    "extract": "EXTRACT",
    "click": "CLICK",
    "note": "NOTE",
    "blocked": "BLOCKED",
    "error": "ERROR",
}


def trace_card(data: Dict[str, Any]) -> str:
    steps = data.get("steps") or []
    lines: List[str] = [
        f"🧭 <b>How I got there</b> · {_hops(len(steps))}",
        "",
    ]
    for i, s in enumerate(steps, 1):
        kind = KIND_LABEL.get(s.get("kind", ""), str(s.get("kind", "")).upper())
        status = f" <i>[{s['status']}]</i>" if s.get("status") else ""
        lines.append(f"{i}. <b>{h(kind)}</b>{status}")
        lines.append(f"   <code>{h(short(s.get('url', ''), 88))}</code>")
        detail = s.get("detail") or ""
        if detail:
            lines.append(f"   <i>{h(short(detail, 100))}</i>")
        lines.append("")
        if sum(len(x) for x in lines) > MAX_LEN:
            lines.append("<i>… rest of the trace cut off</i>")
            break
    lines += ["", FOOTER]
    return "\n".join(lines)[:MAX_LEN].strip()


# ---------------------------------------------------------------- help / start


def help_text(is_owner: bool = False) -> str:
    known = sites.stats()["total"]
    base = (
        "<b>Nick Bypass</b>\n"
        "Paste a shortlink and I'll walk the ad chain for you, then hand back the "
        "page it was hiding.\n\n"
        "<b>Just send it</b>\n"
        "<code>https://gplinks.co/abc123</code>\n"
        "Up to 5 links in one message — spaces or new lines, both fine.\n\n"
        "<b>When you need more</b>\n"
        "· <code>/mode</code> — fast, auto or browser\n"
        f"· <code>/sites</code> — {known:,} shorteners I know by name "
        "(<code>/sites gplinks</code> to search)\n"
        "· <code>/status</code> — engine health, cache, queue\n"
        "· <code>/ip</code> — check whether this machine even needs a proxy\n"
        "· <code>/id</code> — your Telegram ID\n\n"
        "<b>Speed</b>\n"
        "Most links land in under two seconds on the HTTP path. The browser only "
        "wakes up when a gate genuinely needs it, and you can force it with "
        "<code>/mode browser</code>\n\n"
        "<b>What I won't do</b>\n"
        "No captcha farms, no logins, no paywalls, no direct file-host links. And if "
        "a link can't be resolved I'll tell you why — I never invent a destination."
    )
    if is_owner:
        base += (
            "\n\n<b>Admin</b>\n"
            "· <code>/stats</code> — traffic and success rate\n"
            "· <code>/users</code> — user count\n"
            "· <code>/broadcast &lt;text&gt;</code> — message everyone\n"
            "· <code>/ban &lt;id&gt;</code> · <code>/unban &lt;id&gt;</code>\n"
            "· <code>/proxy on IN</code> — build a local IP pool\n"
            "· <code>/proxy scan [CC] [url]</code> — refill and test it\n"
            "· <code>/proxy list</code> · <code>/proxy add ip:port</code>\n"
            "· <code>/proxy &lt;url&gt;</code> — one fixed exit IP · "
            "<code>/proxy clear</code>"
        )
    return base


# ---------------------------------------------------------------- proxy cards


def scan_card(cc: str, rep: Dict[str, Any], st: Dict[str, Any], target: str = "") -> str:
    cand = int(rep.get("candidates") or 0)
    reached = int(rep.get("reached") or 0)
    verified = int(rep.get("verified") or 0)
    kept = int(st.get("total") or 0)
    lines = [
        f"🌾 <b>{h(cc)} proxy pool</b>",
        "",
        f"Looked at <b>{cand}</b> candidates → {reached} answered → "
        f"{verified} really exit from {h(cc)}.",
        f"Kept <b>{kept}</b> · median {st.get('median_ms', 0)} ms"
        + (f" · {rep.get('site_ok', 0)} passed the site test" if target else ""),
        f"<i>Scanned in {_seconds(float(rep.get('seconds') or 0))}.</i>",
    ]
    if kept == 0:
        lines += ["", "<i>Nothing survived this round. Free lists churn fast — "
                      "scan again in a few minutes.</i>"]
    elif int(st.get("residential") or 0) == 0:
        lines += ["", "<i>Everything left is a hosting ASN, so sites that check the "
                      "ISP will still refuse them.</i>"]
    else:
        lines += ["", f"<i>{st.get('residential')} of them look residential — "
                      "those are the ones that matter.</i>"]
    return "\n".join(lines)


def pool_card(rows: List[str], db: Dict[str, Any], country: str, total: int) -> str:
    body = []
    for i, p in enumerate(rows, 1):
        rec = db.get(p, {}) or {}
        bits = [f"{rec.get('latency', '?')} ms"]
        city = rec.get("city")
        if city:
            bits.append(h(str(city)))
        if rec.get("residential"):
            bits.append("🏠")
        if rec.get("site"):
            bits.append("✅")
        body.append(f"{i}. <code>{h(p.split('://')[-1])}</code> · " + " · ".join(bits))
    return (
        f"🌾 <b>{h(country)} pool</b> · {len(rows)} of {total} shown\n\n"
        + "\n".join(body)
        + "\n\n<i>🏠 residential · ✅ passed the site test</i>"
    )


def proxy_card(px: str, farm_line: str, extra: str = "") -> str:
    fixed = h(px.split("@")[-1]) if px else "none"
    lines = [
        "🛰 <b>Exit IP</b>",
        "",
        f"Fixed proxy: <code>{fixed}</code>",
        f"Local pool: {farm_line}",
    ]
    if extra:
        lines.append(extra)
    lines += [
        "",
        "<b>Commands</b>",
        "· <code>/proxy on IN</code> — build a country pool",
        "· <code>/proxy scan IN &lt;url&gt;</code> — refill and test",
        "· <code>/proxy list</code> · <code>/proxy add ip:port</code>",
        "· <code>/proxy http://user:pass@host:port</code> — one fixed IP",
        "· <code>/proxy off</code> — stop rotating",
    ]
    return "\n".join(lines)


def status_card(
    uptime: int, inflight: int, waiting: int, cached: int, hits: int,
    users: int, active: int, today: int, ok_today: int,
    browser: bool, proxy: str, farm: str = "",
) -> str:
    lines = [
        "<b>Status</b>",
        "",
        f"⏱ Up {uptime // 3600}h {uptime % 3600 // 60}m",
        f"⚙️ Working on {inflight} now · {waiting} waiting",
        f"🗄 Cache {cached} links · {hits} served from it",
        f"👥 {users} users · {active} active today",
        f"📊 {today} links today · {ok_today} resolved",
        f"🌐 Browser fallback {'ready' if browser else 'off'}",
        f"🛰 Exit IP {'custom proxy' if proxy else 'server default'}",
    ]
    if farm:
        lines.append(f"🌾 {farm}")
    return "\n".join(lines)


def stats_card(st: Dict[str, Any], top: List[tuple]) -> str:
    rows = "\n".join(f"   {i}. {h(d)} — {n}" for i, (d, n) in enumerate(top, 1)) or "   nothing yet"
    return (
        "<b>Stats</b>\n\n"
        f"👥 {st['users']} users · {st['active_today']} active today\n"
        f"📨 {st['total']} links total · {st['today']} today\n"
        f"✅ {st['success_rate']:.1f}% resolved today\n"
        f"⏱ {st['avg_elapsed']:.1f}s average\n\n"
        f"<b>Busiest sites today</b>\n{rows}"
    )


# ---------------------------------------------------------------- short texts


def ip_card(blocks: List[Dict[str, Any]], verdict: str) -> str:
    """'Do I even need a proxy here?' — answered with what the sites actually see."""
    lines = ["🛰 <b>What the sites see</b>", ""]
    for b in blocks:
        lines.append(f"<b>{h(b.get('label', 'Exit IP'))}</b>")
        if b.get("error"):
            lines += [f"<i>{h(b['error'])}</i>", ""]
            continue
        where = " · ".join(x for x in (b.get("cc"), b.get("city")) if x)
        lines.append(f"<code>{h(b.get('ip', '?'))}</code>" + (f" · {h(where)}" if where else ""))
        net = " · ".join(x for x in (b.get("asn"), b.get("isp")) if x)
        if net:
            lines.append(h(net))
        lines.append(b.get("kind", ""))
        lines.append("")
    lines += ["<b>Verdict</b>", verdict]
    return "\n".join(lines)


def resolving(n: int) -> str:
    if n == 1:
        return "⏳ On it — following the chain…"
    return f"⏳ On it — {n} links, all at once…"


def rate_limited(sec: int) -> str:
    return f"🐢 Easy — one more in {sec}s."
