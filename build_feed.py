#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pearl.de ar- es keszletfeed epito.

Adatforras: www.pearl.de sitemap + termekoldalak schema.org/Product JSON-LD blokkja.
A letoltes streamelve tortenik es megszakad, amint a JSON-LD megvan (~84 KB / 869 KB helyett).

Kimenet: feed/pearl_feed.json es feed/pearl_feed.csv
Futtatas: python build_feed.py [--limit N] [--workers N]
"""

import argparse
import csv
import gzip
import io
import json
import os
import random
import re
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import urllib.request
import urllib.error

# ---------------------------------------------------------------- konfiguracio

BASE = "https://www.pearl.de"
SITEMAP_INDEX = f"{BASE}/sitemapindex.xml"

OUT_DIR = os.environ.get("PEARL_OUT_DIR", "feed")
JSON_PATH = os.path.join(OUT_DIR, "pearl_feed.json")
CSV_PATH = os.path.join(OUT_DIR, "pearl_feed.csv")
STATE_PATH = os.path.join(OUT_DIR, "state.json")

CURRENCY = "EUR"
PRICE_TYPE = "gross_retail"  # brutto, AFA-val, vegfelhasznaloi ar

# --- Silent-failure vedelem (6. pont) ---
MIN_PRODUCTS = int(os.environ.get("PEARL_MIN_PRODUCTS", "12000"))
MAX_MISSING_PRICE_RATIO = float(os.environ.get("PEARL_MAX_MISSING_PRICE", "0.20"))
MAX_SHRINK_RATIO = float(os.environ.get("PEARL_MAX_SHRINK", "0.30"))

# --- Kimeletes tempo: a sajat fix IP-t nem akarjuk kitiltatni ---
WORKERS = int(os.environ.get("PEARL_WORKERS", "3"))
SLEEP_MIN = float(os.environ.get("PEARL_SLEEP_MIN", "0.15"))
SLEEP_MAX = float(os.environ.get("PEARL_SLEEP_MAX", "0.45"))
TIMEOUT = 30
RETRIES = 3
STREAM_CAP = 400_000  # ha eddig nincs JSON-LD, feladjuk az adott lapot

# Veszleallito: ha a szerver tiltani kezd, azonnal allj le
BLOCK_STATUSES = {403, 429}
BLOCK_LIMIT = int(os.environ.get("PEARL_BLOCK_LIMIT", "25"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

BUDAPEST = timezone(timedelta(hours=2))


class FeedError(RuntimeError):
    """Vedelmi szabaly sertes -> nem irunk felul semmit."""


_block_hits = {"n": 0}


# ---------------------------------------------------------------- halozat

def _fetch(url, stream_until=None, timeout=TIMEOUT):
    """Letoltes. Ha stream_until adott, a letoltes megszakad, amint a regex illeszkedik.
    Visszaad: (status, text). A gzip dekodolast kezeli stream kozben is."""
    last_err = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if stream_until is None:
                    raw = r.read()
                    if "gzip" in enc:
                        raw = gzip.decompress(raw)
                    elif "deflate" in enc:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                    return r.status, raw.decode("utf-8", "replace")

                # streamelt olvasas, korai megszakitassal
                if "gzip" in enc:
                    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
                elif "deflate" in enc:
                    dec = zlib.decompressobj(-zlib.MAX_WBITS)
                else:
                    dec = None

                buf = io.StringIO()
                got = 0
                while True:
                    chunk = r.read(32768)
                    if not chunk:
                        break
                    got += len(chunk)
                    if dec is not None:
                        try:
                            piece = dec.decompress(chunk)
                        except zlib.error:
                            piece = b""
                    else:
                        piece = chunk
                    if piece:
                        buf.write(piece.decode("utf-8", "replace"))
                    text = buf.getvalue()
                    if stream_until.search(text):
                        return r.status, text
                    if got > STREAM_CAP:
                        break
                return r.status, buf.getvalue()

        except urllib.error.HTTPError as e:
            if e.code in BLOCK_STATUSES:
                _block_hits["n"] += 1
                if _block_hits["n"] >= BLOCK_LIMIT:
                    raise FeedError(
                        f"VESZLEALLITO: {_block_hits['n']} db {e.code} valasz a pearl.de-rol. "
                        "A szerver valoszinuleg tiltja az IP-t, a futas leall, hogy ne rontsuk el a hozzaferest."
                    )
            last_err = f"HTTP {e.code}"
            if e.code in (404, 410):
                return e.code, ""
        except Exception as e:  # timeout, DNS, reset
            last_err = str(e)
        time.sleep(1.5 * (attempt + 1) + random.random())
    return 0, f"__ERR__{last_err}"


# ---------------------------------------------------------------- sitemap

SKU_RE = re.compile(r"/a-([A-Za-z0-9]+)-\d+\.shtml")


def load_product_urls():
    status, xml = _fetch(SITEMAP_INDEX)
    if status != 200 or not xml or xml.startswith("__ERR__"):
        raise FeedError(f"A sitemapindex.xml nem toltheto le (status={status}).")
    maps = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    pd_maps = [m for m in maps if "sitemappd" in m]
    if not pd_maps:
        raise FeedError("Nem talalhato termek-sitemap (sitemappd*) az indexben.")

    urls = {}
    for sm in pd_maps:
        st, body = _fetch(sm)
        if st != 200 or not body:
            print(f"  FIGYELEM: {sm} nem toltheto le (status={st}), kihagyva")
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body):
            m = SKU_RE.search(loc)
            if m:
                urls.setdefault(m.group(1).upper(), loc.split(";")[0])
        print(f"  {sm}: eddig {len(urls)} egyedi cikkszam")
    if not urls:
        raise FeedError("A sitemapokbol egyetlen termek URL sem nyerhető ki.")
    return urls


# ---------------------------------------------------------------- parsolas

# A JSON-LD (ar, keszlet) a HTML ~8,6%-anal van, a listaar-blokk (statt_preis)
# ~12-17%-anal. A statt_preis div MINDIG kikerul: akcios termeknel tartalmazza a
# listaarat, akciotlannal ures. Addig olvasunk, hogy ez a div lezaruljon, a
# maradek ~85%-ot (leiras, ajanlok, lablec) nem toltjuk le.
LD_TRIGGER = re.compile(
    r'"priceCurrency"\s*:\s*"[A-Z]{3}"'
    r'[\s\S]{0,200000}?class="statt_preis"[\s\S]{0,300}?</div>'
)

RE_PRICE = re.compile(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?')
RE_CURR = re.compile(r'"priceCurrency"\s*:\s*"([A-Z]{3})"')
RE_AVAIL = re.compile(r'"availability"\s*:\s*"([^"]+)"')
RE_NAME = re.compile(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"')
RE_GTIN = re.compile(r'"gtin13"\s*:\s*"([0-9]+)"')
RE_IMG = re.compile(r'"image"\s*:\s*\[\s*"([^"]+)"')
RE_IMG1 = re.compile(r'"image"\s*:\s*"([^"]+)"')
RE_BRAND = re.compile(r'"brand"\s*:\s*\{[^}]*?"name"\s*:\s*"((?:[^"\\]|\\.)*)"')

# listaar / kedvezmeny: a HTML-bol, "best effort" (nincs a JSON-LD-ben).
# Valos struktura: <div class="statt_preis"> statt<sup ...>4</sup>&#32;&euro;&nbsp;<strike>39,96</strike></div>
# Akciotlan termeknel ez a div ures.
RE_STATT_BLOCK = re.compile(r'class="statt_preis"(.{0,400}?)</div>', re.S)
RE_STRIKE = re.compile(r"<strike>\s*([0-9.]*[0-9],[0-9]{2})\s*</strike>")
# suly a JSON-LD leirasbol: "Gewicht: 43 g" / "Gewicht: 1,2 kg"
RE_WEIGHT = re.compile(r"Gewicht[:\s]*(?:ca\.\s*)?([0-9]+(?:[.,][0-9]+)?)\s*(kg|g)\b", re.I)


def _unescape(s):
    if s is None:
        return None
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s.replace('\\"', '"').replace("\\/", "/")


def _num(s):
    """'1.234,56' vagy '75.99' -> float"""
    if s is None:
        return None
    s = s.strip()
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_product(sku, url):
    status, html = _fetch(url, stream_until=LD_TRIGGER)
    if status != 200 or not html or html.startswith("__ERR__"):
        return {"cikkszam": sku, "url": url, "_error": f"status={status}"}

    # A Product blokkot keressuk, ne a szervezet/breadcrumb JSON-LD-t.
    # A leiras nagyon hosszu tud lenni (tapasztalat: 4000+ karakter), ezert
    # a Product blokk vegeig (</script>) vagy nagy ablakig megyunk, kulonben
    # a price/availability kiszorul es hamisan "nincs ar" lesz belole.
    seg = html
    i = html.find('"@type": "Product"')
    if i < 0:
        i = html.find('"@type":"Product"')
    if i >= 0:
        end = html.find("</script>", i)
        seg = html[i:end] if end > i else html[i:i + 30000]

    price = _num((RE_PRICE.search(seg) or [None, None])[1] if RE_PRICE.search(seg) else None)
    curr = (RE_CURR.search(seg).group(1) if RE_CURR.search(seg) else None)
    avail_raw = (RE_AVAIL.search(seg).group(1) if RE_AVAIL.search(seg) else None)
    name = _unescape(RE_NAME.search(seg).group(1)) if RE_NAME.search(seg) else None
    brand = _unescape(RE_BRAND.search(seg).group(1)) if RE_BRAND.search(seg) else None
    gtin = RE_GTIN.search(seg).group(1) if RE_GTIN.search(seg) else None
    img = None
    if RE_IMG.search(seg):
        img = RE_IMG.search(seg).group(1)
    elif RE_IMG1.search(seg):
        img = RE_IMG1.search(seg).group(1)
    # A forras nehany terméknel hibas kepURL-t ad ("https:https://..."), javitjuk
    if img:
        img = img.strip()
        img = re.sub(r"^https?:(?=https?://)", "", img)
        if img.startswith("//"):
            img = "https:" + img
        elif img.startswith("/"):
            img = BASE + img

    in_stock = None
    if avail_raw:
        a = avail_raw.lower()
        if "instock" in a or "limitedavailability" in a or "presale" in a:
            in_stock = True
        elif "outofstock" in a or "soldout" in a or "discontinued" in a or "backorder" in a:
            in_stock = False

    # listaar + kedvezmeny (best effort, a feed magjat nem befolyasolja)
    lista = None
    blk = RE_STATT_BLOCK.search(html)
    if blk:
        ms = RE_STRIKE.search(blk.group(1))
        if ms:
            lista = _num(ms.group(1))

    # suly a leirasbol (best effort), kg-ban normalizalva
    suly = None
    mw = RE_WEIGHT.search(seg)
    if mw:
        val = _num(mw.group(1))
        if val is not None:
            suly = round(val / 1000.0, 4) if mw.group(2).lower() == "g" else round(val, 4)

    # A "% sparen" szoveg a lapon mas (ajanlott) termekhez is tartozhat, ezert
    # CSAK a sajat listaarbol szamolt kedvezmenyben bizunk. Ha nincs listaar,
    # a kedvezmeny is ures marad, mert kulonben rossz szazalekot irnank ki.
    save = None
    akcios = False
    if lista is not None and price is not None:
        if lista > price:
            pct = int(round((lista - price) / lista * 100))
            # Vedelem a hibas parositas ellen: a Pearl neha tobbdarabos csomag
            # osszegzett arat vagy egy szomszedos termek arat teszi a statt
            # blokkba. 95%-nal nagyobb "kedvezmeny" szinte biztosan hibas parositas,
            # ilyenkor inkabb ures listaarat adunk, mint hamis kedvezmenyt.
            if pct <= 95:
                akcios = True
                save = pct
            else:
                lista = None
        else:
            # a forras neha hibas/idegen listaarat mutat (kisebb, mint az ar)
            lista = None

    return {
        "cikkszam": sku,
        "nev": name,
        "marka": brand,
        "aktualis_ar": price,
        "listaar": lista,
        "akcios": akcios,
        "kedvezmeny_szazalek": save,
        "keszleten": in_stock,
        "keszlet_tipus": "van_nincs",  # a Pearl nem ad darabszamot
        "suly": suly,
        "suly_egyseg": "kg" if suly is not None else None,
        "gtin13": gtin,
        "termek_url": url,
        "fokep_url": img,
        "penznem": curr or CURRENCY,
        "_error": None if price is not None else "nincs_ar",
    }


# ---------------------------------------------------------------- duplikatumok

def dedupe(rows):
    """3. pont: ha egy cikkszam tobbszor szerepel, a keszleten levo valtozat marad.
    A vegen a cikkszam garantaltan egyedi, es a volt_duplikalt mezo jelzi az utkozest."""
    by_sku = {}
    counts = {}
    for r in rows:
        sku = r["cikkszam"]
        counts[sku] = counts.get(sku, 0) + 1
        cur = by_sku.get(sku)
        if cur is None:
            by_sku[sku] = r
            continue

        def score(x):
            return (
                1 if x.get("keszleten") is True else 0,       # keszleten levo elonyben
                1 if x.get("aktualis_ar") is not None else 0,  # van ar
                1 if x.get("nev") else 0,
            )

        if score(r) > score(cur):
            by_sku[sku] = r

    out = []
    for sku, r in by_sku.items():
        r = dict(r)
        r["volt_duplikalt"] = counts[sku] > 1
        r["duplikatum_darab"] = counts[sku]
        out.append(r)
    out.sort(key=lambda x: x["cikkszam"])
    return out


# ---------------------------------------------------------------- vedelem

def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def guard(rows, state):
    n = len(rows)
    problems = []

    if n < MIN_PRODUCTS:
        problems.append(f"a termekszam {n}, ami a {MIN_PRODUCTS} minimum alatt van")

    no_price = sum(1 for r in rows if r.get("aktualis_ar") is None)
    ratio = (no_price / n) if n else 1.0
    if ratio > MAX_MISSING_PRICE_RATIO:
        problems.append(
            f"a termekek {ratio*100:.1f}%-anal nincs ar (max {MAX_MISSING_PRICE_RATIO*100:.0f}%)"
        )

    prev = state.get("product_count")
    if prev:
        if n < prev * (1 - MAX_SHRINK_RATIO):
            drop = (1 - n / prev) * 100
            problems.append(
                f"a katalogus {drop:.1f}%-ot zuhant az elozo futashoz kepest ({prev} -> {n}), "
                f"a hatar {MAX_SHRINK_RATIO*100:.0f}%"
            )

    if problems:
        raise FeedError(
            "A vedelmi szabalyok nem teljesultek, ezert az utolso jo feed marad kint. Okok: "
            + "; ".join(problems)
        )
    return {"product_count": n, "no_price": no_price, "missing_price_ratio": round(ratio, 4)}


# ---------------------------------------------------------------- kiiras

CSV_COLS = [
    "cikkszam", "nev", "marka", "aktualis_ar", "listaar", "akcios",
    "kedvezmeny_szazalek", "keszleten", "keszlet_tipus", "suly",
    "gtin13", "termek_url", "fokep_url", "penznem",
    "volt_duplikalt", "duplikatum_darab", "utolso_modositas",
]


def write_outputs(rows, stats, started, finished):
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = finished.isoformat()
    for r in rows:
        r.setdefault("suly", None)
        r["utolso_modositas"] = ts
        r.pop("_error", None)

    in_stock = sum(1 for r in rows if r.get("keszleten") is True)
    out_stock = sum(1 for r in rows if r.get("keszleten") is False)
    unknown = len(rows) - in_stock - out_stock
    on_sale = sum(1 for r in rows if r.get("akcios"))
    dups = sum(1 for r in rows if r.get("volt_duplikalt"))
    prices = [r["aktualis_ar"] for r in rows if r.get("aktualis_ar") is not None]

    feed = {
        "feed_info": {
            "forras": "https://www.pearl.de",
            "adatforras_tipus": "sitemap + schema.org/Product JSON-LD",
            "penznem": CURRENCY,
            "ar_tipus": PRICE_TYPE,
            "ar_megjegyzes": "Brutto, AFA-val terhelt kiskereskedelmi (vegfelhasznaloi) ar. NEM netto beszallitoi ar.",
            "keszlet_megjegyzes": "A forras csak van/nincs keszletet ad, darabszam nem elerheto.",
            "suly_megjegyzes": "A forras a strukturalt adatban nem ad szallitasi sulyt, ezert a suly mezo ures.",
            "generalva": ts,
            "generalas_kezdete": started.isoformat(),
            "futasi_ido_sec": int((finished - started).total_seconds()),
            "azonosito_kulcs": "cikkszam",
            "frissites": "3 naponta 22:00 (Europe/Budapest)",
            "termekek_szama": len(rows),
            "keszleten": in_stock,
            "nincs_keszleten": out_stock,
            "keszlet_ismeretlen": unknown,
            "akcios_termekek": on_sale,
            "duplikalt_cikkszamok": dups,
            "ar_nelkul": stats.get("no_price", 0),
            "ar_min": min(prices) if prices else None,
            "ar_max": max(prices) if prices else None,
        },
        "termekek": rows,
    }

    tmp = JSON_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
    os.replace(tmp, JSON_PATH)

    tmpc = CSV_PATH + ".tmp"
    with open(tmpc, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# forras=https://www.pearl.de; penznem={CURRENCY}; ar_tipus={PRICE_TYPE} "
                f"(brutto kiskereskedelmi, AFA-val); keszlet=van/nincs (nem darabszam); "
                f"generalva={ts}; termekek={len(rows)}; keszleten={in_stock}\n")
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore", delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmpc, CSV_PATH)

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"product_count": len(rows), "generalva": ts,
                   "keszleten": in_stock, "akcios": on_sale}, f, indent=1)

    return feed["feed_info"]


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="csak az elso N termek (teszthez)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    started = datetime.now(BUDAPEST)
    print(f"[{started:%Y-%m-%d %H:%M:%S}] pearl.de feed epites indul")

    print("Sitemapok olvasasa...")
    urls = load_product_urls()
    items = sorted(urls.items())
    if args.limit:
        items = items[:args.limit]
    print(f"Osszesen {len(items)} termek URL feldolgozasra")

    rows = []
    errors = 0
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for sku, url in items:
            futs[ex.submit(parse_product, sku, url)] = sku
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX) / max(args.workers, 1))

        for fut in as_completed(futs):
            done += 1
            try:
                r = fut.result()
            except FeedError:
                raise
            except Exception as e:
                errors += 1
                continue
            if r.get("_error") and r.get("aktualis_ar") is None and not r.get("nev"):
                errors += 1
            rows.append(r)
            if done % 500 == 0:
                rate = done / max(time.time() - t0, 1)
                eta = (len(items) - done) / max(rate, 0.01) / 60
                print(f"  {done}/{len(items)}  ({rate:.1f}/s, ETA {eta:.0f} perc, hiba: {errors})")

    print(f"Letoltes kesz: {len(rows)} sor, {errors} hibas")

    rows = dedupe(rows)
    print(f"Duplikatum-kezeles utan: {len(rows)} egyedi cikkszam")

    state = load_state()
    stats = guard(rows, state)

    finished = datetime.now(BUDAPEST)
    info = write_outputs(rows, stats, started, finished)

    print("\n=== FEED KESZ ===")
    for k in ("termekek_szama", "keszleten", "nincs_keszleten", "keszlet_ismeretlen",
              "akcios_termekek", "duplikalt_cikkszamok", "ar_nelkul", "futasi_ido_sec"):
        print(f"  {k}: {info[k]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FeedError as e:
        print(f"\nHIBA (vedelem aktivalt): {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
