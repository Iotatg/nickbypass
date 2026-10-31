# Indian proxy pool — what was built and what actually works

Date: 2026-10-31 · Tested against `https://vplink.in/nAKfh`

## 1. The list you sent

`https://pastes.io/OC4cX0t8` is behind a Cloudflare managed challenge (both plain
HTTP and a real headless Chromium get "Performing security verification"), so the
content could not be read. The screenshot of it is readable, though, and it shows
the problem immediately:

```
10.6.20.x:62790   10.8.125.x:62540   10.96.147.x:2540   10.28.125.x:994
```

`10.0.0.0/8` is RFC1918 **private** space. Those addresses only exist inside
whoever's LAN produced the list; from the public internet they are unroutable.
A "350 proxies / 200 TB bandwidth" paste built on 10.x addresses cannot work, no
matter how fast the line is. The harvester now rejects them explicitly
(`tests/test_farm.py` → "拒绝 10.x 私网").

## 2. Can I "build" Indian proxies?

An IP address can only be allocated by the ISP that owns the block. Nobody can
mint one in a script. What *can* be built — and now is — is the pool around them:

`nickbot/proxyfarm.py`

```
harvest  →  exit-IP  →  country + ISP  →  site probe  →  rank  →  persist
7 public   ask the     ip-api batch      GET the real   residential
lists,     proxy its   lookup, keep      target URL,    first, then
country=IN own IP      only cc==IN       drop walls     latency
```

Details that matter:

- **Exit-IP first, not list labels.** Lists lie about country constantly. Each
  candidate is asked `api.ipify.org` *through itself*; the returned IP is then
  batch-resolved locally (100 IPs per call) for `countryCode` + `isp`.
- **ISP check, not just country.** Hosting ASNs (DigitalOcean, Oracle, AWS…) are
  flagged; Indian telcos/broadband (Jio, Airtel, BSNL, ACT, Hathway, Excitel…)
  are marked residential and ranked first.
- **Site probe before use.** A proxy that opens google but is walled on vplink is
  worthless, so the real target URL is fetched through the proxy first.
- **Hard timeouts.** SOCKS handshakes ignore httpx timeouts, so every probe is
  wrapped in `asyncio.wait_for` — no request can hang the resolver.
- **Persistence + self-refill.** `data/proxies_in.json`, 5-minute per-host reuse,
  background refresh every 20 minutes, 2-strike eviction.

Bot side: `/proxy on IN`, `/proxy scan IN <url>`, `/proxy list`, `/proxy add
ip:port`, `/proxy off`, plus farm stats in `/status`. The resolver only reaches
for the farm when a site actually walls the IP, so an unwalled link costs 0 ms
extra.

## 3. Measured reality (free Indian proxies)

| Stage | Count |
|---|---|
| candidates harvested | ~350 |
| answered at all (exit-IP call) | ~60–85 |
| really exiting from India | 1–3 |
| passed `vplink.in` | 0–1 |
| still alive 10 minutes later | usually 0 |

Free proxies are one-shot: they answer the first request, then die mid-chain.
A gate chain is 5–8 hops, so this is a lottery, not a solution. The farm makes the
lottery cheap (probe first, never start a chain a dead proxy can't finish) and
free, but it cannot manufacture supply.

## 4. Why vplink really fails (measured)

| Exit IP | Result |
|---|---|
| This sandbox (datacenter, EU) | `200` + **VPN Detected** page (`.vpn-container`) |
| DigitalOcean **Mumbai** (139.59.1.14) | `403 Forbidden`, 9 bytes |
| Indian residential / mobile | gate opens, chain resolves |

So it is an **ASN** check, not a country check. An Indian VPS does not help —
tested, it 403s. Also tested and ruled out:

- `POST /links/go` with a valid CSRF token harvested from the un-walled homepage →
  `{"status":"success","url":"#"}` — the visit is rejected server-side, the API
  itself is not a bypass.
- Indian proxies that *did* pass the gate resolved the chain (one RTDS Mumbai IP
  did), which is how the ad-bounce bug below was found.

**The only reliable fix:** a residential/mobile Indian IP — a paid proxy
(`PROXY=...`) or running the bot on an Indian home/mobile connection.

## 5. Bug found and fixed while testing

With a working Indian exit, the chain ended as:

```
vplink.in/nAKfh → targetssc.com/…/article → 301 → pipefitter.in/…   ← reported as "destination"
```

The real destination was the `targetssc.com` article; `pipefitter.in` is an ad
layer that bounces the user back. The resolver now stops when a terminal page
redirects *into* a known ad host, and falls back to the deepest clean hop
(`test_chain.py` case 4).

## 6. Tests

```
test_offline.py  36   formatting, extraction, rate limit, DB, escaping
test_fast.py     16   gate API, meta/JS redirect, VPN wall, dead destination
test_chain.py    10   full 3-hop chain + ad-layer bounce
test_farm.py     25   proxy parsing (RFC1918 rejected), ranking, persistence
                 ---
                 87   all passing
```
