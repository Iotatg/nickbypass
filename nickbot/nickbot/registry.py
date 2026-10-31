"""Site data: what a domain is, and which road to take with it.

Two layers, deliberately kept apart:

``CURATED``
    ~180 domains a human vetted. Each one carries a real display name and a
    strategy tag, because the strategy is what actually makes a bypass fast:

    ``gt``     same PHP script as GPLinks — ``POST /links/go`` returns
               ``{"status":"success","url":...}``. One request, no browser.
    ``gate``   countdown / "click to continue" ad gate. HTTP can read the
               page, but only a real browser gets past the timer.
    ``short``  plain 301/302 redirector. No browser, ever.

``BULK`` (``domains.txt``)
    Everything else we can *recognise* as a shortener, harvested from public
    lists and DNS-checked at build time (see ``tools/build_sites.py``).
    Recognition matters: it stops the resolver from handing back an ad page
    as the "destination", and lets the bot name the site instead of printing
    a bare hostname. It does **not** promise a hand-written parser.

Both layers are just data. The generic engine in ``gates.py`` is what
resolves links; a domain missing from here still goes through it.
"""

from __future__ import annotations

from pathlib import Path

# (domain, display name, kind) — keep alphabetical-ish inside each block
CURATED: tuple[tuple[str, str, str], ...] = (
    # ── GT-Links script family: POST /links/go returns the destination ──
    ("gplinks.co", "GPLinks", "gt"),
    ("gplinks.in", "GPLinks", "gt"),
    ("vplink.in", "VPLink", "gt"),
    ("vplinks.in", "VPLinks", "gt"),
    ("arolinks.com", "AroLinks", "gt"),
    ("atglinks.com", "ATG Links", "gt"),
    ("xtglinks.com", "XTG Links", "gt"),
    ("srnky.com", "Srnky", "gt"),
    ("try2link.com", "Try2Link", "gt"),
    ("liteshort.com", "LiteShort", "gt"),
    ("inshorturl.com", "InShortURL", "gt"),
    ("vipshort.in", "VipShort", "gt"),
    ("cpmlink.net", "CPMLink", "gt"),
    ("cpmlink.co", "CPMLink", "gt"),
    ("cpmlink.pro", "CPMLink", "gt"),
    ("earn4link.in", "Earn4Link", "gt"),
    ("earnlinks.in", "EarnLinks", "gt"),
    ("babylinks.in", "BabyLinks", "gt"),
    ("easysky.in", "EasySky", "gt"),
    ("linkpays.in", "LinkPays", "gt"),
    ("linksfire.co", "LinksFire", "gt"),
    ("lksfy.com", "LKSFY", "gt"),
    ("rempo.xyz", "Rempo", "gt"),
    ("kogap.xyz", "Kogap", "gt"),
    ("aylink.co", "Aylink", "gt"),
    ("tii.la", "Tii.la", "gt"),
    ("tpi.li", "TPI", "gt"),
    ("oii.la", "Oii.la", "gt"),
    ("oii.io", "Oii.io", "gt"),
    ("cuty.io", "Cuty.io", "gt"),
    ("exe.io", "Exe.io", "gt"),
    ("clk.sh", "Clk.sh", "gt"),
    ("za.gl", "Za.gl", "gt"),
    ("droplink.co", "DropLink", "gt"),
    ("tech8s.net", "Tech8s", "gt"),
    ("game5s.com", "Game5s", "gt"),
    ("shortxlinks.com", "ShortXLinks", "gt"),
    ("shortxlinks.in", "ShortXLinks", "gt"),
    ("shrinkme.io", "ShrinkMe", "gt"),
    ("shrinkearn.com", "ShrinkEarn", "gt"),
    ("shrinkforearn.in", "ShrinkForEarn", "gt"),
    ("safelinkearn.com", "SafeLinkEarn", "gt"),
    ("paid4link.com", "Paid4Link", "gt"),
    ("loot-link.com", "Loot-Link", "gt"),
    ("lootlinks.co", "LootLinks", "gt"),
    ("lootlink.org", "LootLink", "gt"),
    ("links-loot.com", "Links Loot", "gt"),
    ("v2links.me", "V2Links", "gt"),
    ("link2unlock.com", "Link2Unlock", "gt"),
    ("sub2unlock.com", "Sub2Unlock", "gt"),
    ("sub4unlock.com", "Sub4Unlock", "gt"),
    ("work.ink", "Work.ink", "gt"),
    ("workink.click", "Workink", "gt"),
    ("rekonise.com", "Rekonise", "gt"),
    ("shortmoz.link", "Shortmoz", "gt"),
    ("indianshortner.com", "IndianShortner", "gt"),
    ("vipurl.in", "VipURL", "gt"),
    ("clicksfly.com", "ClicksFly", "gt"),
    ("mdiskshortner.link", "MDisk Shortner", "gt"),
    ("get2.in", "Get2", "gt"),
    ("powergam.online", "PowerGam", "gt"),

    # ── classic JS ad gates (countdown / button), browser needed ──
    ("adf.ly", "AdF.ly", "gate"),
    ("adfoc.us", "AdFoc.us", "gate"),
    ("adrinolinks.com", "AdrinoLinks", "gate"),
    ("adshnk.com", "Adshnk", "gate"),
    ("adsfly.in", "AdsFly", "gate"),
    ("adcryp.to", "Adcryp.to", "gate"),
    ("adtival.network", "Adtival", "gate"),
    ("alpharede.com", "AlphaRede", "gate"),
    ("bc.vc", "BC.VC", "gate"),
    ("bindaaslinks.com", "BindaasLinks", "gate"),
    ("boost.ink", "Boost.ink", "gate"),
    ("bstlar.com", "Bstlar", "gate"),
    ("claimclicks.com", "ClaimClicks", "gate"),
    ("clk.ink", "Clk.ink", "gate"),
    ("coinclix.co", "CoinClix", "gate"),
    ("cpmshort.com", "CPMShort", "gate"),
    ("cutlink.net", "CutLink", "gate"),
    ("cutnet.net", "CutNet", "gate"),
    ("cuttlinks.com", "CuttLinks", "gate"),
    ("cuttyy.com", "Cuttyy", "gate"),
    ("dlink2.com", "DLink", "gate"),
    ("dlink4.com", "DLink", "gate"),
    ("drop.carbikenation.com", "Drop (Carbike Nation)", "gate"),
    ("earnlink.in", "EarnLink", "gate"),
    ("flycutlink.com", "FlyCutLink", "gate"),
    ("get-to.link", "Get-To", "gate"),
    ("golink.pro", "GoLink", "gate"),
    ("gplinks.net", "GPLinks", "gate"),
    ("hypershort.com", "HyperShort", "gate"),
    ("icerik.site", "Icerik", "gate"),
    ("iflylink.com", "IFlyLink", "gate"),
    ("indiaearnx.in", "IndiaEarnX", "gate"),
    ("link-to.net", "Link-To", "gate"),
    ("link1s.com", "Link1s", "gate"),
    ("link4m.com", "Link4m", "gate"),
    ("linkify.ru", "Linkify", "gate"),
    ("linkjust.com", "LinkJust", "gate"),
    ("linkpoi.me", "LinkPoi", "gate"),
    ("linksflys.com", "LinksFlys", "gate"),
    ("linkshortx.in", "LinkShortX", "gate"),
    ("linksloot.net", "LinksLoot", "gate"),
    ("linkvertise.com", "Linkvertise", "gate"),
    ("linkvertise.net", "Linkvertise", "gate"),
    ("liteurl.in", "LiteURL", "gate"),
    ("lootdest.com", "LootDest", "gate"),
    ("lootdest.info", "LootDest", "gate"),
    ("lootdest.org", "LootDest", "gate"),
    ("makelinks.in", "MakeLinks", "gate"),
    ("mboost.me", "MBoost", "gate"),
    ("mdn.lol", "MDN", "gate"),
    ("nowshort.com", "NowShort", "gate"),
    ("oko.sh", "Oko.sh", "gate"),
    ("ouo.io", "Ouo.io", "gate"),
    ("ouo.press", "Ouo.press", "gate"),
    ("profitshort.com", "ProfitShort", "gate"),
    ("remso.xyz", "Remso", "gate"),
    ("seturl.in", "SetURL", "gate"),
    ("sfl.gl", "SFL", "gate"),
    ("shorte.st", "Shorte.st", "gate"),
    ("shortencash.click", "ShortenCash", "gate"),
    ("shortenbuddy.com", "ShortenBuddy", "gate"),
    ("shorterall.com", "ShorterAll", "gate"),
    ("shortly.xyz", "Shortly", "gate"),
    ("shortnest.com", "ShortNest", "gate"),
    ("shrink.icu", "Shrink.icu", "gate"),
    ("shrinke.me", "Shrinke.me", "gate"),
    ("snipcash.com", "SnipCash", "gate"),
    ("soo.gd", "Soo.gd", "gate"),
    ("srt.am", "Srt.am", "gate"),
    ("stfly.me", "StFly", "gate"),
    ("stly.link", "Stly", "gate"),
    ("submais.com", "Submais", "gate"),
    ("tei.ai", "Tei.ai", "gate"),
    ("teralink.me", "TeraLink", "gate"),
    ("teraearn.com", "TeraEarn", "gate"),
    ("terashare.me", "TeraShare", "gate"),
    ("tfly.link", "TFly", "gate"),
    ("techurlshort.in", "TechURLShort", "gate"),
    ("tl.gd", "TL.gd", "gate"),
    ("tlkm.id", "TLKM", "gate"),
    ("tnlink.in", "TNLink", "gate"),
    ("uiil.ink", "Uiil", "gate"),
    ("uiz.io", "Uiz", "gate"),
    ("unlocktoearn.com", "UnlockToEarn", "gate"),
    ("upshrink.com", "UpShrink", "gate"),
    ("us.to", "US.to", "gate"),
    ("v2links.in", "V2Links", "gate"),
    ("xpshort.com", "XPShort", "gate"),
    ("zeilink.net", "Zeilink", "gate"),
    ("zovo.to", "Zovo", "gate"),

    # ── plain shorteners: one redirect, no browser needed ──
    ("amzn.to", "Amzn.to", "short"),
    ("bit.ly", "Bit.ly", "short"),
    ("bitly.com", "Bitly", "short"),
    ("buff.ly", "Buff.ly", "short"),
    ("clck.ru", "Clck.ru", "short"),
    ("cutt.ly", "Cutt.ly", "short"),
    ("dlvr.it", "Dlvr.it", "short"),
    ("fb.me", "FB.me", "short"),
    ("goo.gl", "Goo.gl", "short"),
    ("ift.tt", "Ift.tt", "short"),
    ("is.gd", "is.gd", "short"),
    ("kutt.it", "Kutt", "short"),
    ("lnkd.in", "LinkedIn", "short"),
    ("ow.ly", "Ow.ly", "short"),
    ("rb.gy", "RB.gy", "short"),
    ("rebrand.ly", "Rebrand.ly", "short"),
    ("s.id", "S.id", "short"),
    ("shorturl.at", "ShortURL", "short"),
    ("shorturl.gg", "ShortURL.gg", "short"),
    ("shrtco.de", "Shrtco", "short"),
    ("surl.li", "Surl.li", "short"),
    ("t.co", "t.co", "short"),
    ("t.ly", "T.ly", "short"),
    ("t2m.io", "T2M", "short"),
    ("tidd.ly", "Tidd.ly", "short"),
    ("tiny.cc", "TinyCC", "short"),
    ("tiny.one", "Tiny.one", "short"),
    ("tinyurl.com", "TinyURL", "short"),
    ("trib.al", "Trib.al", "short"),
    ("u.to", "U.to", "short"),
    ("urlz.fr", "Urlz.fr", "short"),
    ("v.gd", "v.gd", "short"),
    ("vk.cc", "VK", "short"),
    ("wp.me", "WP.me", "short"),
    ("x.co", "X.co", "short"),
)

# Getting a direct link out of these needs a site scraper (file hosts / piracy
# sites). This bot only unwraps redirects.
SCRAPER_ONLY: tuple[str, ...] = (
    "hubcloud", "hubdrive", "gdflix", "gdtot", "zdrive", "katdrive",
    "toonworld4all", "animekaizou", "filepress", "drivebot",
)

BULK_PATH = Path(__file__).with_name("domains.txt")


def bulk_domains() -> tuple[str, ...]:
    """Generated list; missing file is not fatal (bot still works)."""
    try:
        raw = BULK_PATH.read_text(encoding="utf-8")
    except OSError:
        return ()
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return tuple(out)
