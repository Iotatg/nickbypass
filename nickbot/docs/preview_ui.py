"""Render the bot's message copy as a Telegram-style mock, for eyeballing layout."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/workspace/nickbot")

from nickbot import format as F  # noqa: E402

OK1 = {
    "input": "https://gplinks.co/aBc12X",
    "final": "https://drive.google.com/file/d/1Kx9mQ/view?usp=sharing",
    "ok": True, "method": "http", "elapsed": 0.84, "note": "",
    "steps": [{"kind": "request", "url": "a"}, {"kind": "extract", "url": "b"},
              {"kind": "redirect", "url": "c"}],
}
OK2 = {
    "input": "https://arolinks.com/9f2k1",
    "final": "https://example.com/watch/season-2/ep-07",
    "ok": True, "method": "browser", "elapsed": 6.24, "note": "Resolved with browser",
    "steps": [{"kind": "request", "url": "a"}, {"kind": "click", "url": "b"},
              {"kind": "click", "url": "c"}, {"kind": "redirect", "url": "d"},
              {"kind": "redirect", "url": "e"}],
}
FAIL_CF = {
    "input": "https://ouo.io/Qm4tzP", "final": "https://ouo.io/Qm4tzP", "ok": False,
    "method": "http", "elapsed": 3.4,
    "note": "Cloudflare challenge (403 just a moment)", "steps": [{"kind": "request", "url": "a"}],
}
FAIL_VPN = {
    "input": "https://vplink.in/nAKfh", "final": "https://vplink.in/nAKfh", "ok": False,
    "method": "http", "elapsed": 0.67, "blocked": "vpn",
    "note": "Site blocks datacenter / VPN IPs", "steps": [{"kind": "request", "url": "a"}],
}
TRACE = {
    "steps": [
        {"kind": "request", "url": "https://vplink.in/nAKfh", "status": 200, "detail": "HTML gate page"},
        {"kind": "extract", "url": "https://vplink.in/nAKfh", "detail": "meta refresh -> pipefitter.in"},
        {"kind": "redirect", "url": "https://pipefitter.in/studyinsurances/", "status": 302, "detail": "HTTP redirect"},
        {"kind": "click", "url": "https://targetssc.com/article?id=9", "detail": "button #getlink"},
    ]
}

JIO = {"label": "Direct — the machine running the bot", "ip": "49.36.12.84",
       "cc": "IN", "city": "Mumbai", "asn": "AS55836",
       "isp": "Reliance Jio Infocomm Limited", "kind": "🏠 home or mobile line"}
IP_VERDICT = ("You're on an Indian home line, so vplink, gplinks and friends open "
              "without any proxy. Keep the pool off with <code>/proxy off</code> — "
              "your own IP is better than anything it can find.")

CARDS = [
    ("result", F.result_card(OK1, 1, 1), ["🧭 Path", "🔗 Open"]),
    ("result", F.result_card(OK2, 2, 3), ["🧭 Path", "🔗 Open"]),
    ("result", F.result_card(FAIL_CF, 1, 1), ["🧭 Path", "🌐 Try browser"]),
    ("result", F.result_card(FAIL_VPN, 1, 1), ["🧭 Path"]),
    ("trace", F.trace_card(TRACE), []),
    ("scan", F.scan_card("IN", {"candidates": 352, "reached": 61, "verified": 3,
                                "site_ok": 1, "seconds": 52.4},
                         {"total": 3, "median_ms": 240, "residential": 2},
                         "https://vplink.in/nAKfh"), []),
    ("pool", F.pool_card(
        ["http://45.194.41.70:8080", "socks5://139.59.1.14:1080", "http://103.87.12.9:3128"],
        {"http://45.194.41.70:8080": {"latency": 210, "city": "Mumbai", "residential": True, "site": True},
         "socks5://139.59.1.14:1080": {"latency": 388, "city": "Bengaluru", "residential": True},
         "http://103.87.12.9:3128": {"latency": 640, "city": "Delhi"}},
        "IN", 34), []),
    ("ip", F.ip_card([JIO], IP_VERDICT), []),
    ("status", F.status_card(7 * 3600 + 42 * 60, 2, 1, 148, 902, 311, 44, 1204, 987,
                             True, "", "IN pool · 34 IPs · 9 site-tested · 240 ms"), []),
    ("help", F.help_text(False), []),
    ("mode", "⚡ <b>Fast mode</b>\nHTTP only. Quick, but a gate that runs a countdown will beat you.", []),
]

CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#0e1621; font-family:-apple-system,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;
       color:#e9edf0; -webkit-font-smoothing:antialiased; }
.wrap { width: 460px; margin: 0 auto; padding: 26px 14px 40px; }
.top { position:sticky; top:0; background:rgba(14,22,33,.94); backdrop-filter:blur(6px);
       padding:14px 6px 10px; margin:-26px -14px 18px; border-bottom:1px solid #1c2a38;
       display:flex; align-items:center; gap:10px; z-index:5; }
.avatar { width:34px; height:34px; border-radius:50%; background:linear-gradient(135deg,#2f9cf4,#1f6fd0);
          display:flex; align-items:center; justify-content:center; font-size:17px; }
.who { font-weight:600; font-size:15px; }
.sub { font-size:12px; color:#6d7f8f; }
.bubble { background:#182533; border-radius:14px 14px 14px 4px; padding:10px 13px 9px;
          margin: 0 0 4px; max-width:100%; font-size:14.5px; line-height:1.45; word-wrap:break-word; }
.bubble b { font-weight:600; color:#fff; }
.bubble i { color:#93a3b3; font-style:italic; }
.bubble code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px;
               background:rgba(0,0,0,.34); border-radius:5px; padding:2px 5px; color:#c7e3ff;
               word-break:break-all; }
.kb { display:flex; gap:6px; margin:0 0 22px 0; }
.kb button { flex:1; background:#1f2f40; color:#62bcf9; border:none; border-radius:9px;
             padding:9px 6px; font-size:13.5px; font-family:inherit; }
.label { font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:#54687a;
         margin:26px 0 8px 4px; }
"""


def bubble(html: str) -> str:
    return f'<div class="bubble">{html.replace(chr(10), "<br>")}</div>'


def build() -> str:
    parts = [f"<style>{CSS}</style>", '<div class="wrap">',
             '<div class="top"><div class="avatar">⚡</div>'
             '<div><div class="who">Nick Bypass</div>'
             '<div class="sub">bot</div></div></div>']
    last = None
    for kind, text, buttons in CARDS:
        if kind != last:
            titles = {"result": "Result cards", "trace": "Path / trace", "scan": "Pool scan",
                      "pool": "Pool list", "status": "Status", "help": "Start screen", "ip": "Exit IP check",
                      "mode": "Mode switch"}
            parts.append(f'<div class="label">{titles[kind]}</div>')
            last = kind
        parts.append(bubble(text))
        if buttons:
            parts.append('<div class="kb">' + "".join(f"<button>{b}</button>" for b in buttons) + "</div>")
        else:
            parts.append('<div style="height:16px"></div>')
    parts.append("</div>")
    return "<!doctype html><html><head><meta charset='utf-8'></head><body>" + "".join(parts) + "</body></html>"


if __name__ == "__main__":
    out = Path("/workspace/nickbot/docs")
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / "ui-preview.html"
    html_path.write_text(build(), encoding="utf-8")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 460, "height": 1200}, device_scale_factor=2)
        pg.goto(html_path.as_uri())
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(out / "ui-preview.png"), full_page=True)
        b.close()
    print("ok", out / "ui-preview.png")
