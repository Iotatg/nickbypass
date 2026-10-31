# Nick Bypass Bot

Telegram bot that resolves shortlinks and ad-gate links to their real destination.

**v2 — fast path first, browser only when it is actually needed.**

```
✅ 1/1 · VPLink

Original Link : https://vplink.in/nAKfh
Bypassed Link : https://auth.pwasmultiverse.workers.dev/generate?code=...

Time Taken : 0.42 seconds
Method : ⚡ Site API · 2 hops
```

---

## How it resolves a link

| Stage | What happens | Typical time |
|---|---|---|
| **1. Fast HTTP** | Shared `httpx.AsyncClient` (HTTP/2 + keep-alive), follows the redirect chain and extracts the next hop from `meta refresh` / `location.href` / inline JSON / base64 params / forms | 0.1 – 2 s |
| **2. Site API** | For the "GT Links" family (`vplink`, `gplinks`, `arolinks`, `liteshort`, `vipshort`, …) it calls the site's own `POST /links/go`, which returns `{"status":"success","url":"…"}` — no browser at all | 0.3 – 1 s |
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
| `/supported` | known sites |
| `/status` | uptime, in-flight, cache, proxy |
| `/proxy <url>` · `/proxy clear` | owner only — fixed exit IP, no restart |
| `/proxy on [IN]` · `/proxy off` | owner only — turn the country IP pool on/off |
| `/proxy scan [IN] [url]` | owner only — refill + verify the pool now |
| `/proxy list` · `/proxy add ip:port` · `/proxy del ip:port` | owner only — inspect/edit the pool |
| `/stats` `/users` `/ban` `/unban` `/broadcast` | owner only |
| `/id` | your ID + chat ID |

Every result has a **🧭 Trace** button (full hop-by-hop trace) and **🔗 Open**.

## Run

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
  handlers.py    commands, parallel processing, buttons
  format.py      message cards (HTML-escaped)
  sites.py       domain registry (60+ gates, 10 shorteners)
  db.py          sqlite: users, requests, bans
tests/
  test_fast.py   gate API / meta / JS / VPN wall / dead destination / concurrency
  test_farm.py   proxy parsing (RFC1918 rejected), pool ranking, persistence
  test_offline.py formatting, URL parsing, rate limit, DB, handler wiring
```

## Tests

```bash
python3 tests/test_fast.py       # 16 checks — fast path, site API, VPN wall, dead destination
python3 tests/test_offline.py    # 36 checks — extraction, routing, rate limit, DB, escaping
python3 tests/test_chain.py      # 10 checks — 3-hop gate chain, ad-layer bounce
python3 tests/test_farm.py       # 25 checks — proxy parsing, pool ranking, persistence
```

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
