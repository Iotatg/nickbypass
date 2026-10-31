# Nick Bypass Bot

Telegram bot that resolves ad-gate / link-shortener chains (gplinks, vplink,
arolinks, liteshort, earn4link, …) down to the real destination, and says so
honestly when a site refuses the server's IP.

```
nickbypass/
├── nickbot/          Telegram bot (Kurigram or python-telegram-bot, sqlite, httpx)
│   ├── nickbot/      config, transport, resolver, gates, proxy farm, handlers
│   ├── tests/        186 offline checks (no network needed)
│   ├── DEPLOY.md     Termux / VPS / Windows / macOS / Docker / Pi / PaaS
│   └── README.md     commands, config, how it resolves
└── linkresolver/     standalone resolution engine (HTTP + Playwright)
```

## Quick start

```bash
cd nickbot
pip install -r requirements.txt
playwright install chromium
cp .env.example .env          # BOT_TOKEN + OWNER_ID
python3 -m nickbot --check
python3 -m nickbot
```

The bot runs on **Kurigram** (MTProto) when `API_ID` / `API_HASH` are set, and on
**python-telegram-bot** otherwise — same handlers, same cards, one config line
(`BACKEND=auto|kurigram|ptb`).

`linkresolver` is picked up from `../linkresolver` by default
(`LINKRESOLVER_HOME` overrides it).

Running it on a phone, a VPS, Windows, Docker or a Pi? **[nickbot/DEPLOY.md](nickbot/DEPLOY.md)**
has copy-paste steps for each, including Termux on Android.

## What it does

- Fast path: async HTTP with its own cookie jar, 30x, `meta refresh`, JS
  `location`, base64 params, inline JSON, `/links/go` site API (including the
  `csrfToken` + `X-CSRF-TOKEN` double submit and the countdown the GT template
  waits for).
- Browser fallback only when a page genuinely needs JS/timers (Playwright).
- Detects and reports real walls: Cloudflare challenge, "VPN detected",
  `403` on datacenter ASNs — instead of inventing a result.
- Country proxy farm (`/proxy on IN`): harvest → exit-IP → country/ISP check →
  site probe → ranked pool, auto-refreshed in the background.

## Tests

```bash
cd nickbot
python3 tests/test_offline.py   # 56
python3 tests/test_fast.py      # 16
python3 tests/test_chain.py     # 10
python3 tests/test_farm.py      # 25
```

## Scope

No captcha solving, no login/paywall bypass, no file-host direct links. It
resolves public ad-gate chains and reports failures with a reason.

## Note

`.env` is git-ignored on purpose. Never commit a bot token — revoke it with
@BotFather if it ever leaks.
