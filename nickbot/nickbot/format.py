"""Message formatting. Everything user-supplied is escaped (HTML injection)."""

from __future__ import annotations

from typing import Any, Dict, List

from . import sites

MAX_LEN = 3800
FOOTER = "─────────────────\n⚡ @nova93ec2c4_bot"


def h(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def short(url: str, n: int = 72) -> str:
    url = url or ""
    return url if len(url) <= n else url[: n - 1] + "…"


def _method(method: str) -> str:
    return {
        "http": "⚡ Fast HTTP",
        "gate": "⚡ Site API",
        "browser": "🌐 Browser",
        "rules": "📐 Rule match",
    }.get(method or "", "❔ Unknown")


def _seconds(sec: float) -> str:
    if sec <= 0:
        return "0 seconds"
    if sec < 1:
        return f"{sec:.2f} seconds"
    return f"{sec:.1f} seconds"


def result_card(data: Dict[str, Any], idx: int = 1, total: int = 1) -> str:
    """One result -> HTML text."""
    url = data.get("input", "")
    site = sites.display_name(url)
    ok = bool(data.get("ok"))
    final = data.get("final", "")
    steps = data.get("steps") or []
    elapsed = float(data.get("elapsed") or 0.0)
    method = data.get("method", "")
    note = data.get("note", "")
    blocked = data.get("blocked", "")

    tag = f"<b>{idx}/{total}</b> · <i>{h(site)}</i>"
    if data.get("cached"):
        tag += " · <i>(cached)</i>"

    if ok and final:
        lines = [
            f"✅ {tag}",
            "",
            f"<b>Original Link</b> : <code>{h(short(url, 200))}</code>",
            f"<b>Bypassed Link</b> : <code>{h(short(final, 400))}</code>",
            "",
            f"<b>Time Taken</b> : {_seconds(elapsed)}",
            f"<b>Method</b> : {_method(method)} · {len(steps)} hops",
        ]
        if note:
            lines.append(f"<i>{h(short(note, 140))}</i>")
        lines += ["", FOOTER]
        return "\n".join(lines)

    lines = [f"❌ <b>Could not resolve</b> · <i>{h(site)}</i>", ""]
    lines.append(f"<b>Original Link</b> : <code>{h(short(url, 200))}</code>")
    if note:
        lines.append(f"<b>Reason</b> : {h(short(note, 150))}")
    if final and final != url:
        lines.append(f"<b>Stopped at</b> : <code>{h(short(final, 200))}</code>")
    hint = _hint(blocked, note)
    if hint:
        lines += ["", f"<i>{hint}</i>"]
    lines.append("")
    lines.append(f"<b>Time Taken</b> : {_seconds(elapsed)} · {_method(method)}")
    lines += ["", FOOTER]
    return "\n".join(lines)


def _hint(blocked: str, note: str) -> str:
    """一行可执行建议，不写小作文。"""
    if blocked == "vpn":
        return "Needs a local/residential IP — /proxy on IN builds an Indian pool, or set PROXY."
    low = (note or "").lower()
    if "captcha" in low or "challenge" in low or "anti-bot" in low:
        return "Anti-bot wall — try /mode browser, or retry later."
    if "timeout" in low or "timed out" in low or "connect" in low:
        return "Site did not answer in time — retry, or switch to /mode browser."
    if "http 4" in low or "http 5" in low or "dead" in low:
        return "Link looks dead, or the site is down right now."
    if "browser" in low:
        return "Try /mode browser for a full browser run."
    return ""


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
    lines: List[str] = ["🧭 <b>Redirect trace</b>", ""]
    for i, s in enumerate(data.get("steps") or [], 1):
        kind = KIND_LABEL.get(s.get("kind", ""), str(s.get("kind", "")).upper())
        status = f" [{s['status']}]" if s.get("status") else ""
        detail = s.get("detail") or ""
        lines.append(
            f"{i}. <b>{h(kind)}</b> <code>{h(short(s.get('url', ''), 90))}</code>{h(status)}"
        )
        if detail:
            lines.append(f"    <i>{h(short(detail, 110))}</i>")
        if sum(len(x) for x in lines) > MAX_LEN:
            lines.append("… (truncated)")
            break
    return "\n".join(lines)[:MAX_LEN]


def help_text(is_owner: bool = False) -> str:
    base = (
        "<b>Nick Bypass Bot</b>\n"
        "Send me a shortlink or ad-gate link and I'll dig out the destination.\n\n"
        "<b>How to use</b>\n"
        "· Just paste the link (up to 5 per message)\n"
        "· <code>/bypass &lt;url&gt;</code> explicit call\n"
        "· <code>/mode</code> switch fast / auto / browser\n"
        "· <code>/supported</code> see known sites\n"
        "· <code>/id</code> your Telegram ID\n\n"
        "<b>Speed</b>\n"
        "· Fast HTTP + site-API path: usually 0.2–2s\n"
        "· Browser fallback only when a gate really needs it\n\n"
        "<b>What it won't do</b>\n"
        "· No captcha solving, no login/paywall bypass\n"
        "· No file-host direct links (HubCloud / GDFlix etc.)\n"
        "· If it can't resolve, it says why — it never invents a link"
    )
    if is_owner:
        base += (
            "\n\n<b>Admin</b>\n"
            "· <code>/stats</code> stats　<code>/users</code> user count\n"
            "· <code>/broadcast &lt;text&gt;</code> send to all\n"
            "· <code>/ban &lt;id&gt;</code> / <code>/unban &lt;id&gt;</code>\n"
            "· <code>/proxy on IN</code> build Indian IP pool　<code>/proxy scan</code> refill\n"
            "· <code>/proxy list</code> pool　<code>/proxy add ip:port</code>　<code>/proxy off</code> stop\n"
            "· <code>/proxy &lt;url&gt;</code> fixed exit proxy　<code>/proxy clear</code> remove it\n"
            "· <code>/status</code> runtime status"
        )
    return base
