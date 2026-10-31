# Link-bypass projects on GitHub that actually run - survey

> Surveyed 2026-10-31. One criterion only: **readable source + real logic + still
> works today.** Anything packed with Pyarmor, an encrypted blob, an .exe only,
> or "contact me on TG for the new build" does not count.

---

## 1. There are only three kinds of implementation

| Kind | How it works | What it gets through | State |
|---|---|---|---|
| **A. Plain HTTP unwrapping** | follow 301/302, `meta refresh`, `location.href`, base64 parameters | ad-free shorteners: bit.ly / tinyurl / is.gd / v.gd / t.co | stable, always works |
| **B. Per-site handler + third-party API** | a parser per site (ouo, gdtot, katdrive ...), `cloudscraper` for CF, `bypass.vip` / `dcrypt.it` for the hard ones | the sites that existed in 2021-2022 | **mostly dead**: redesigns, paid APIs, CF upgrades |
| **C. Browser automation / userscript** | open a real browser, wait the countdown, click Continue, kill popups, capture the final URL | Linkvertise / GPLinks / ouo / adfoc and other JS gates | the only general answer left, but slow (5-25 s per link) |

**The key finding**: modern gates (Linkvertise, GPLinks, ouo, shrinkme) build the
destination at runtime, with dynamic tokens and reCAPTCHA. A pure `requests`
approach has been dead since 2023 - which is exactly why those 2022 repos with
thousands of stars no longer work.

---

## 2. Repos worth reading (ordered by usefulness)

### 1. `nOneCode4u/bypass-shortlinks` - closest to "actually usable"
- **Shape**: single-file userscript, 303 KB, v96.5 (still updated 2026-10-30), Unlicense
- **How**: 400+ domain rules, in-browser DOM automation (wait countdowns, click buttons, decode base64, handle meta, skip safelinks)
- **Good**: actively maintained, widest rule set, no server dependency
- **Bad**: Tampermonkey only, no API/CLI; rules are tuned site by site, so redesigns need follow-up
- **Fits**: skipping gates while you browse yourself

### 2. `TheCaduceus/Link-Bypasser-Bot` - 64 stars, Python, 2022-11
- **How**: one function per site in `bypasser.py` (katdrive / hubdrive / gdtot / ouo ...), `cloudscraper` for Cloudflare,
  the heavy lifting handed to `bypass.vip`, `dcrypt.it`, `xcscxr/ouo-bypass`
- **Verdict**: clean structure, the textbook sample of kind B; but most 2022 handlers are dead now and it depends
  hard on third-party APIs (when they die, it dies). Good to **read**, bad to deploy.

### 3. `Mid0aria/universal-bypass` - 26 stars, Python, 2023-05
- Single file `universalbypass.py`, interactive CLI, rules in a dict. Light and readable, a decent template.

### 4. `TWIST-X7/Aio-Bypasser` - 50 stars, C# / .NET, 2022
- Desktop GUI, 17 sites. Windows only, needs the .NET SDK to build. Abandoned.

### 5. `tsubasa652/python-shortlink-extractor` - MIT, 2022
- The only one properly packaged on pip: `extract_short_link(url)`. Kind A only (plain unwrapping), ~200 lines, clean.

### 6. Others (smaller, same idea)
`bipinkrish/Link-Bypasser-Bot` (TG bot), `Luiss156/link-gate-jumper`, `Suurp/ShorLink-Master`,
`cyan-n1d3/PSAbypass`, `nsmle/ouo-bypass`, `wendoj/linkvertise-bypasser`.

### The counter-example
That `ig-magic/NickBypassbot` from earlier: Pyarmor encryption + Windows `.pyd` + "leaked private tool" marketing,
and the plain source in its history is a prank script ("There is no bypass. There never was."). **Never touch that kind.**

---

## 3. So this is what got built

Kinds A and C combined into one auditable local tool (`./linkresolver/`):

1. **HTTP fast path** (milliseconds): follow 30x by hand, one trace entry per hop, then try
   `meta refresh` -> JS `location` -> the url field of inline JSON -> base64 parameters (`?url= ?r= ?s=`) ->
   a "continue" button -> an auto-submitted GET form. adf.ly's `ysmm` variable has its own static decoder.
2. **Browser fallback** (seconds): when the page is judged to be a gate, start headless Chromium, block ad and
   analytics domains, wait the countdown, click "Continue / Get Link", capture the first address after leaving
   the gate hosts.
3. **No lying**: Cloudflare challenges, login walls and dead links are reported as "stuck here", never turned
   into an invented link - the usual failure mode of most bots.
4. **Two entry points**: CLI (`python -m linkresolver`) + web UI (`uvicorn linkresolver.server:app`).

Measured results are in the "Verified locally" section of `README.md`.
