# Nick Bypass Bot

Telegram bot that resolves shortlinks and ad-gate links to their real destination.

**v4 — two backends (Kurigram / PTB), and the gate handshake done properly.**

```
✅ Link bypassed · GPLinks

You sent
https://gplinks.co/aBc12X

Real destination
https://drive.google.com/file/d/1Kx9mQ/view

Straight through the gate — that's the real destination.

0.84s · direct HTTP · 3 hops
─────────────────
⚡ @nova93ec2c4_bot
```

When it can't get through, it explains the wall in plain words and offers the fix
instead of dumping a status code:

```
⚠️ Couldn't get through · VPLink

You sent
https://vplink.in/nAKfh

What happened
This site checks the IP the request comes from and turns down datacenter
and VPN addresses. Nothing on our side can disguise that.

What to try
/proxy on IN builds a pool of local IPs. A residential proxy in PROXY works
too, if you have one.

0.67s · direct HTTP · 1 hop
```

`docs/ui-preview.png` is a full render of every card the bot can send.

---

## How it resolves a link

| Stage | What happens | Typical time |
|---|---|---|
| **1. Fast HTTP** | Shared `httpx.AsyncClient` (HTTP/2 + keep-alive), follows the redirect chain and extracts the next hop from `meta refresh` / `location.href` / inline JSON / base64 params / forms | 0.1 – 2 s |
| **2. Site API** | For the "GT Links" family (`vplink`, `gplinks`, `arolinks`, `liteshort`, `vipshort`, …) it calls the site's own `POST /links/go`. The template wants a `csrfToken` cookie **and** a matching `X-CSRF-TOKEN` header (double submit, any value passes) and the countdown finished — until then it answers `{"url":"#"}`. The bot mints the pair, waits out the timer, retries, and only trusts a real `http…` URL | 1 – 6 s |
| **3. Browser** | Only if the page really needs it (countdown, JS-built URL, click gate). Headless Chromium, ads blocked, waits out the timer, clicks Continue | 4 – 12 s |
| **4. Proxy retry** | If the site answers with a *"VPN detected / disable VPN"* wall, the bot knows the exit IP is blacklisted and stops immediately instead of burning 30 s — then optionally retries through a proxy | — |

**Nothing is invented.** If it can't get through, it says exactly where it stopped and why.

## Commands

| Command | Description |
|---|---|
| `/start`, `/help` | usage |
| `/bypass <url>`, `/b` | explicit call |
| *(just paste a link)* | auto-detect, up to 5 per message, resolved in parallel |
| `/mode auto\|fast\|browser` | per-user strategy |
| `/sites` | the shortener list — `/sites gplinks` searches it |
| `/status` | uptime, in-flight, cache, proxy |
| `/ip` | what the sites see — exit IP, ASN, and whether a proxy is needed here |
| `/proxy <url>` · `/proxy clear` | owner only — fixed exit IP, no restart |
| `/proxy on [IN]` · `/proxy off` | owner only — turn the country IP pool on/off |
| `/proxy scan [IN] [url]` | owner only — refill + verify the pool now |
| `/proxy list` · `/proxy add ip:port` · `/proxy del ip:port` | owner only — inspect/edit the pool |
| `/stats` `/users` `/ban` `/unban` `/broadcast` | owner only |
| `/id` | your ID + chat ID |

Every result carries buttons that depend on what happened:

| Button | When | What it does |
|---|---|---|
| **🧭 Path** | always | hop-by-hop trace — every request, redirect, extract and click |
| **🔗 Open** | on success | jumps straight to the destination |
| **🌐 Try browser** | only on failure | re-runs the *same* link with Chromium, bypassing the cache — no need to retype `/mode browser` |

The command menu is set from code (`/start` shows the user set, owners get the admin
set automatically), so a fresh deploy looks right without manual BotFather work.

## Sites

The bot knows ~2,400 shortener domains by name. That's two layers, kept apart on purpose:

| Layer | Where | What it buys |
|---|---|---|
| Hand-written | `nickbot/registry.py` — 186 domains | a real display name *and* a strategy: `gt` (site API, sub-second), `gate` (browser), `short` (plain redirect) |
| Generated | `nickbot/domains.txt` — 2,234 domains | recognition only: the bot can name the site, and it won't hand an ad page back as the "destination" |

The generated list is rebuilt from public shortener catalogues — HaGeZi's
`urlshortener` blocklist and uBlock Origin's link-shortener filters — folded to
registrable domains and DNS-checked, so dead sites don't get claimed:

```bash
python3 tools/build_sites.py            # rebuild + verify (needs network)
python3 tools/build_sites.py --no-dns   # skip the liveness pass
```

Being on the list is a convenience, not a requirement: anything unlisted still
goes through the same engine. `/sites` counts what the bot **knows**, not what it
can **try**.


## Backends

The command layer is framework-free (`transport.py` wraps whichever library is
running), so both backends share every handler, every card and every test.

| | **Kurigram** (default when configured) | **python-telegram-bot** |
|---|---|---|
| Protocol | MTProto | HTTP Bot API |
| Credentials | `BOT_TOKEN` + `API_ID` + `API_HASH` | `BOT_TOKEN` only |
| Updates | push, lower latency | long polling |
| Install | `pip install kurigram` | already in requirements |

`BACKEND=auto` (the default) picks Kurigram when `API_ID`/`API_HASH` are set and
falls back to PTB otherwise, so a token-only deploy keeps working.

To use Kurigram: open <https://my.telegram.org> → *API development tools*,
create an app, then put the two values in `.env`:

```ini
BACKEND=kurigram
API_ID=1234567
API_HASH=0123456789abcdef0123456789abcdef
```

`python3 -m nickbot --check` prints which backend it will use.

## Run

> Every platform — Termux/Android, VPS + systemd, Windows, macOS, Docker,
> Raspberry Pi, PaaS — has a copy-paste guide in **[DEPLOY.md](DEPLOY.md)**.

```bash
cd /workspace/nickbot
pip install -r requirements.txt
playwright install chromium

cp .env.example .env      # fill BOT_TOKEN + OWNER_ID
python3 -m nickbot --check
python3 -m nickbot
```

Control script:

```bash
./botctl.sh start|stop|restart|status|log
```

## Config

| Key | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | — | from @BotFather |
| `BACKEND` | `auto` | `kurigram` / `ptb` / `auto` |
| `API_ID` / `API_HASH` | — | Kurigram only, from <https://my.telegram.org> |
| `OWNER_ID` | — | comma-separated numeric IDs |
| `LOGGER_CHAT_ID` | — | error log chat (bot must be a member) |
| `RATE_LIMIT` / `RATE_WINDOW` | `8` / `60` | per-user quota |
| `MAX_URLS` | `5` | links per message |
| `HTTP_TIMEOUT` | `12` | fast-path timeout (s) |
| `BROWSER_TIMEOUT` | `20` | browser budget (s) |
| `MAX_HOPS` | `12` | redirect depth |
| `HTTP_CONCURRENCY` | `6` | parallel resolves |
| `ALLOW_BROWSER` | `true` | global browser switch |
| `PROXY` | — | `http://user:pass@host:port` — **the real fix for IP-gated sites** |
| `PROXY_FARM` | `false` | keep a verified pool of local exit IPs (`IN` by default) |
| `PROXY_COUNTRY` | `IN` | country whose IPs the pool must actually exit from |
| `PROXY_BUDGET` | `12` | seconds allowed for the "swap IP and retry" pass |
| `AUTO_PROXY` | `false` | legacy free-proxy rotation (unverified; farm is better) |

### About IP-gated sites

`vplink.in` and friends refuse IPs they don't like server-side. Two separate
walls, measured in October 2026:

| Exit IP | Result |
|---|---|
| This sandbox (datacenter, EU) | `200` page with **VPN Detected** |
| DigitalOcean **Mumbai** | `403 Forbidden` (9 bytes) |
| Indian residential / mobile | gate opens, chain resolves in ~1–2 s |

So it is not a country check — it is an **ASN check**. An Indian VPS does not
help; only a residential/mobile Indian IP does.

```bash
# real fix: one residential IP, set once
PROXY=http://user:pass@residential-host:port
```

### Do I need a proxy? (running it yourself)

If you run the bot on your own machine in India, on a normal broadband or mobile
connection: **no**. Your ISP ASN is residential, so `vplink`, `gplinks` and the
rest open on the first request. Adding a proxy there only makes it slower.

```bash
# on your own connection
PROXY=
PROXY_FARM=false
```

Ask the bot with `/ip` — it prints the exit IP, the ASN and the verdict:

```
🛰 What the sites see

Direct — the machine running the bot
49.36.12.84 · IN · Mumbai
AS55836 · Reliance Jio Infocomm Limited
🏠 home or mobile line

Verdict
You're on an Indian home line, so vplink, gplinks and friends open without any
proxy. Keep the pool off with /proxy off — your own IP is better than anything
it can find.
```

You **do** need one when the answer is `🏢 datacenter / hosting` — a VPS, a cloud
box, a home server behind a hosting provider, or a VPN. Same for the country: a
residential line outside India passes some gates but not the ones that check geo.

**The farm (`PROXY_FARM=true`) is the free fallback.** It harvests Indian
candidates from public lists, asks each proxy for its own exit IP, batch-checks
that IP's country *and ISP* (residential vs hosting), then probes the actual
target URL before use. Survivors are ranked residential-first and reused for
5 minutes per host; the pool refills itself in the background every 20 minutes.

Reality check on free Indian proxies (measured): ~350 candidates → ~60 answer at
all → 1–3 really exit from India → 0–1 pass `vplink`. They die within minutes.
The farm is therefore opportunistic, not a guarantee: it turns an occasional win
into a win instead of wasting 10 s on a dead proxy, and it costs nothing when a
site isn't walled.

**If you need this to work every time:** a paid residential/mobile proxy, or run
the bot on an Indian home/mobile connection. There is no free way to mint
residential IPs — only the ISP that owns the block can hand one out. Lists that
advertise "350 proxies / 200 TB" often contain RFC1918 addresses like
`10.6.20.1:62790`, which are LAN-only and cannot be used from outside; the
harvester rejects them (`tests/test_farm.py` covers this).

**Why `AUTO_PROXY` is off by default.** Tested against `vplink.in`: public
proxies answer the *first* request and then die (`200` → `ConnectTimeout` on the
second). A real gate chain is 5–8 hops, so one-shot proxies can never finish it.
`PROXY_FARM` replaces it: every candidate is site-probed *before* it is handed to
the resolver, so the resolver never starts a chain it can't finish.

## Layout

```
nickbot/
  gates.py       fast async resolver + site-API adapters + block detection
  proxypool.py   legacy free-proxy rotation (unverified)
  proxyfarm.py   country IP pool: harvest → exit-IP → country/ISP → site probe → persist
  resolver.py    orchestration: fast → gate → browser → proxy retry, cache, dedupe
  handlers.py    commands, parallel processing, retry/browser buttons
  format.py      every word the bot says — cards, help, trace, pool/status views
  sites.py       lookup + strategy + /sites text, over the two layers below
  registry.py    hand-written domains: name + strategy (gt / gate / short)
  domains.txt    generated recognition list (2,234 domains, DNS-checked)
  db.py          sqlite: users, requests, bans
tools/
  build_sites.py rebuild domains.txt from public shortener catalogues
tests/
  test_fast.py     gate API / meta / JS / VPN wall / dead destination / concurrency
  test_farm.py     proxy parsing (RFC1918 rejected), pool ranking, persistence
  test_offline.py  formatting, URL parsing, rate limit, DB, handler wiring
  test_registry.py 2,400+ domains: no dupes, lookup rules, terminal logic, speed
```

## Tests

```bash
python3 tests/test_gate.py       # 19 checks — CSRF handshake, countdown, decoy anchors
python3 tests/test_fast.py       # 19 checks — fast path, site API, VPN wall, dead destination
python3 tests/test_offline.py    # 61 checks — extraction, routing, rate limit, DB, copy, escaping
python3 tests/test_chain.py      # 10 checks — 3-hop gate chain, ad-layer bounce
python3 tests/test_farm.py       # 25 checks — proxy parsing, pool ranking, persistence
python3 tests/test_registry.py   # 52 checks — 2,400+ domains, lookup, terminal logic, speed
```

186 checks, no network needed.

`test_gate.py` is the one that matters for the bug people hit: a local server
imitates a GT gate that refuses `/links/go` without the CSRF pair, answers `#`
until the timer runs out, and carries a "sponsored" anchor to pipefitter.in.
The resolver has to come back with the real destination and nothing else.

`test_chain.py` rebuilds the real `vplink.in → pipefitter.in → destination` chain locally
(escaped `\/` in the JS, "Please Wait.." / "Landing.." copy) and proves the resolver
follows it in under 2 s. So when a real link fails, it is the exit IP — not the parser.

## Deploy (systemd)

```ini
[Unit]
Description=Nick Bypass Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/nickbot
ExecStart=/usr/bin/python3 -m nickbot
Restart=always
RestartSec=5
EnvironmentFile=/opt/nickbot/.env

[Install]
WantedBy=multi-user.target
```

## Scope

It expands redirect chains. It does **not** solve captchas, bypass logins or
paywalls, and it does not scrape direct links out of file hosts (HubCloud,
GDFlix, ZDrive …) — those requests are answered with an explicit refusal.
