# linkresolver

Resolve short links and ad gates to the **real destination**: the HTTP fast path
first, and when it gets stuck a headless Chromium waits out the countdown and
clicks through. Every hop leaves a trace, and when it cannot get through it says
where it stopped instead of inventing a link.

```
https://v.gd/CCGtUT
  └─ 200  → page button "https://example.com/hello" points at the destination
       └─ 200  https://example.com/hello   ✓
```

---

## Quick start

```bash
cd linkresolver
pip install -r requirements.txt          # httpx / bs4 / playwright / fastapi / uvicorn
playwright install chromium              # if the system has no chromium

# command line
python -m linkresolver https://bit.ly/xxxx
python -m linkresolver url1 url2 url3 --json
python -m linkresolver https://gplinks.co/xxxx --browser      # force browser mode
python -m linkresolver https://ouo.io/xxxx --no-browser       # HTTP only, fast
python -m linkresolver https://x.y/z --headful                # show the window (debug)

# web UI
python -m uvicorn linkresolver.server:app --port 8000
# open http://localhost:8000
```

As a library:

```python
from linkresolver import resolve

r = resolve("https://tinyurl.com/xxxx")
print(r.ok, r.final_url, r.method)
for s in r.steps:
    print(s.kind, s.url, s.detail)
```

---

## How it resolves

### Layer 1: fast HTTP path (milliseconds, no browser)

Redirects are followed hop by hop by hand (`follow_redirects=False`) and every
hop is recorded as a trace entry. Once a 200 arrives, these are tried in order:

| # | Technique | Covers |
|---|---|---|
| 1 | Site-specific rules | the `ysmm` variable on `adf.ly/j.gs/q.gs` (odd/even split + base64), `vk.com/away.php?to=`, `google/url?q=` |
| 2 | base64 parameters | `?url= ?u= ?r= ?s= ?id= ?go= ?to= ?link= ?dest=` - most safelinks use one of these |
| 3 | `meta http-equiv=refresh` | old-school redirect pages, tinyurl's `/preview/deprecated/` |
| 4 | JS `location` assignment | `location.href=` / `location.replace()` / `top.location=` |
| 5 | Inline JSON | a `{"url":"https://..."}` embedded in `<script>` |
| 6 | Page buttons | an `<a>` whose text is "Continue / Get Link / Download" or the URL itself (v.gd works this way) |
| 7 | GET forms | hidden inputs are turned into a query string and submitted (POST forms are left to the browser) |

### Layer 2: browser fallback (5-25 s)

Triggered when the page looks like an ad gate (known-domain list, or "still on
the original site + timer/countdown/continue button" scoring >= 3):

- headless Chromium, real UA / locale / 1366x800
- `window.open` stubbed to a no-op, so every popup dies
- routing blocks 40+ ad domains (doubleclick, popads, propellerads, adsterra ...)
- every 0.9 s it scans for a visible, enabled element whose text matches
  `continue|get link|proceed|download|verify` and clicks it
- recognises an `N seconds` countdown and waits it out
- destination = the first address **after leaving the gate hosts**; still on the
  gate (`/explore`, a 404 page) counts as unresolved

### Anti-bot detection

`403/429/503` plus `just a moment` / `cf-chl` / `checking your browser` /
`g-recaptcha` is marked as blocked, and the trace says which marker matched.

---

## Verified locally

**16 offline checks pass** (`python3 tests/test_offline.py`): meta refresh / JS
relative paths / base64 (with and without padding) / adf.ly ysmm round trip /
biglink button / GET form query building / anti-bot detection / static asset
filtering.

**Real short links** (resolved live from the sandbox):

| Input | Result | Path | Time |
|---|---|---|---|
| `tinyurl.com/yjqg3sug` | `https://example.com/hello` | http (302 -> preview page -> meta refresh) | 0.4s |
| `is.gd/q7x9eD` | `https://example.com/hello` | http (301) | 0.3s |
| `v.gd/CCGtUT` | `https://example.com/hello` | http (button on an intermediate page) | 0.5s |
| `linkvertise.com/123456/test` (invalid ID) | unresolved | browser | 2.0s |
| `ouo.io/testabc` | blocked by Cloudflare (403 just a moment) | - | 0.1s |

**Browser path**: two local gates were built for comparison - `gate.html` (3 s
countdown + Get Link button) and `gate2.html` (destination assembled at runtime,
no complete URL anywhere in the static HTML). The second one is correctly
reported as "needs a browser" under `--no-browser`, and with the default mode it
falls back automatically: wait 3 s -> click Continue -> land on `target.html`,
5.3 s.

> The sandbox IP is refused by the real Linkvertise / ouo, so end-to-end
> verification of a live ad gate needs your own link from your own machine.

---

## Layout

```
linkresolver/
├── linkresolver/
│   ├── engine.py     # hop-by-hop requests + "destination / gate / blocked" verdict
│   ├── rules.py      # 7 extractors + site rules + domain knowledge
│   ├── browser.py    # Playwright: countdowns, button clicks, destination capture
│   ├── models.py     # Step / Result
│   ├── cli.py        # command line
│   └── server.py     # FastAPI + single-page UI
├── tests/
│   ├── test_offline.py
│   └── fixtures/     # gate.html / gate2.html / target.html
├── SURVEY.md         # what other GitHub projects do
└── requirements.txt
```

---

## Limits (important)

- **No captcha solving.** reCAPTCHA / hCaptcha / Turnstile stop it, and it says so.
- **No login or paywall bypass.** A page that needs an account just stops there.
- **It only unwraps redirect chains.** No proxying, no downloads, no link
  storage; the web UI has no database and writes no logs.
- Use it on links you opened yourself. Using it to farm ad revenue, bypass paid
  content or scrape someone else's files is on you.
