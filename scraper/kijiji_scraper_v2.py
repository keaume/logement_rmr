"""
Scraper Kijiji v2 — plus agressif, plus de régions
- 40 pages max par région
- 8 régions couvrant toute la RMR
- Délai réduit à 2s
- Parse Schema.org + __NEXT_DATA__ + fallback regex prix
"""

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from collections import Counter

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.kijiji.ca"

# ── 8 régions RMR complètes ───────────────────────────────────────────────────
REGIONS = {
    "montreal":         "/b-appartement-condo/ville-de-montreal/c37l1700281",
    "laval":            "/b-appartement-condo/laval-rive-nord/c37l1700281",
    "longueuil":        "/b-appartement-condo/longueuil-rive-sud/c37l1700281",
    "rive_sud":         "/b-appartement-condo/rive-sud-montreal/c37l1700281",
    "rive_nord":        "/b-appartement-condo/rive-nord/c37l1700281",
    "ouest_ile":        "/b-appartement-condo/ouest-de-l-ile/c37l1700281",
    "vaudreuil":        "/b-appartement-condo/vaudreuil/c37l1700281",
    "saint_jean":       "/b-appartement-condo/saint-jean-sur-richelieu/c37l1700281",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.kijiji.ca/",
}

BASE_DIR        = Path(__file__).resolve().parent
CACHE_PATH      = BASE_DIR / "data" / "kijiji_cache.json"
OUTPUT_PATH     = BASE_DIR / "data" / "loyers_par_quartier.json"
GEOCODE_CACHE   = BASE_DIR / "data" / "geocode_cache.json"

DELAY           = 2.0
MAX_PAGES       = 40
MAX_ZERO_NEW    = 3

NOMINATIM_BASE    = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {"User-Agent": "projet-logement-rmr/1.0"}
GEOCODE_DELAY     = 1.1

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path):
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def safe(val):
    if val is None: return ""
    s = html.unescape(str(val)).strip()
    return "" if s.lower() in {"none","null",""} else s

def parse_price(val):
    if not val: return None
    s = re.sub(r"[^\d.]", "", str(val).replace(",","").replace("\xa0",""))
    m = re.search(r"(\d+)", s)
    if m:
        v = float(m.group(1))
        if 400 <= v <= 8000:
            return v
    return None

TYPE_PATTERNS = [
    (r"\b1\s*[½1/2]\b|studio|bachelor",          "1 1/2"),
    (r"\b2\s*[½1/2]\b",                           "2 1/2"),
    (r"\b3\s*[½1/2]\b|1\s*bed",                   "3 1/2"),
    (r"\b4\s*[½1/2]\b|2\s*bed",                   "4 1/2"),
    (r"\b5\s*[½1/2]\b|3\s*bed",                   "5 1/2"),
    (r"\b6\s*[½1/2]\b|4\s*bed",                   "6 1/2"),
]

def normalize_type(text):
    if not text: return None
    t = safe(text).lower().replace(",",".")
    for pattern, label in TYPE_PATTERNS:
        if re.search(pattern, t):
            return label
    return None

# ── Parsing Schema.org ────────────────────────────────────────────────────────

def parse_schema(html_text, region):
    soup = BeautifulSoup(html_text, "html.parser")
    listings = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except:
            continue
        if data.get("@type") != "ItemList":
            continue

        for wrap in data.get("itemListElement", []):
            item = wrap.get("item", {})
            offers = item.get("offers", {})
            price = parse_price(offers.get("price") or offers.get("lowPrice"))
            if not price:
                continue

            addr = item.get("address", {})
            locality = safe(addr.get("addressLocality") if isinstance(addr, dict) else addr)
            street   = safe(addr.get("streetAddress","") if isinstance(addr, dict) else "")
            postal   = safe(addr.get("postalCode","") if isinstance(addr, dict) else "")

            adresse_brute = ", ".join(filter(None, [street, locality, postal, "Québec", "Canada"]))
            if not adresse_brute:
                adresse_brute = region.replace("_"," ").title() + ", Québec, Canada"

            titre = safe(item.get("name",""))
            desc  = safe(item.get("description",""))
            url   = item.get("url","")
            lid   = url.split("/")[-1].split("?")[0] if url else str(hash(titre))

            listings.append({
                "id": lid, "prix": price, "titre": titre,
                "type_logement": normalize_type(f"{titre} {desc}"),
                "url": url, "region": region,
                "adresse_brute": adresse_brute,
                "ville_brute": locality or region.replace("_"," ").title(),
                "quartier_brut": locality or street or region.replace("_"," ").title(),
                "lat": None, "lon": None,
                "quartier_polygonal": None, "ville_polygonale": None,
                "scraped_at": datetime.now().isoformat(),
            })

    return listings

# ── Parsing __NEXT_DATA__ ─────────────────────────────────────────────────────

def parse_next_data(html_text, region):
    soup = BeautifulSoup(html_text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return []

    try:
        data = json.loads(script.string or "")
    except:
        return []

    ads = []
    try:
        pp = data["props"]["pageProps"]
        for key in ["adList","ads","listings","results"]:
            if key in pp:
                ads = pp[key]; break
        if not ads and "initialState" in pp:
            st = pp["initialState"]
            for key in ["adList","ads","listings"]:
                if key in st:
                    ads = st[key]; break
    except:
        pass

    listings = []
    for ad in ads:
        if not isinstance(ad, dict): continue
        price = parse_price((ad.get("price") or {}).get("amount") or ad.get("price"))
        if not price: continue

        loc      = ad.get("location") or {}
        street   = safe(loc.get("mapAddress",""))
        locality = safe(loc.get("name","") or ad.get("locationName",""))
        postal   = safe(loc.get("postalCode",""))

        adresse_brute = ", ".join(filter(None, [street, locality, postal, "Québec", "Canada"]))
        if not adresse_brute:
            adresse_brute = region.replace("_"," ").title() + ", Québec, Canada"

        titre = safe(ad.get("title",""))
        desc  = safe(ad.get("description",""))
        url   = ad.get("seoUrl") or ad.get("url","")
        if url.startswith("/"): url = BASE_URL + url
        lid   = str(ad.get("id") or ad.get("adId") or hash(titre))

        listings.append({
            "id": lid, "prix": price, "titre": titre,
            "type_logement": normalize_type(f"{titre} {desc}"),
            "url": url, "region": region,
            "adresse_brute": adresse_brute,
            "ville_brute": locality or region.replace("_"," ").title(),
            "quartier_brut": locality or street or region.replace("_"," ").title(),
            "lat": None, "lon": None,
            "quartier_polygonal": None, "ville_polygonale": None,
            "scraped_at": datetime.now().isoformat(),
        })

    return listings

# ── Scraping ──────────────────────────────────────────────────────────────────

def scrape_page(url, session):
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  ⚠️ {e}")
        return None

def scrape_region(name, path, cache, session):
    print(f"\n📍 {name}")
    new_count = 0
    zero_streak = 0

    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}{path}?page={page}"
        print(f"  p{page}...", end=" ", flush=True)

        html_text = scrape_page(url, session)
        if not html_text:
            print("err"); break

        listings = parse_schema(html_text, name) or parse_next_data(html_text, name)

        if not listings:
            print("vide"); break

        added = sum(1 for l in listings if l["id"] not in cache and not cache.update({l["id"]: l}))
        new_count += added
        print(f"{len(listings)}({added}n)", end=" ")

        if added == 0:
            zero_streak += 1
        else:
            zero_streak = 0

        if page > 5 and zero_streak >= MAX_ZERO_NEW:
            print("→stop"); break

        time.sleep(DELAY)

    print()
    return new_count

# ── Géocodage ─────────────────────────────────────────────────────────────────

def geocode(address, cache):
    if not address: return None
    if address in cache:
        return tuple(cache[address]) if cache[address] else None

    try:
        r = requests.get(
            f"{NOMINATIM_BASE}/search",
            params={"q": address, "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS, timeout=15,
        )
        results = r.json()
        if results:
            lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
            cache[address] = [lat, lon]
            time.sleep(GEOCODE_DELAY)
            return lat, lon
        cache[address] = None
        return None
    except Exception as e:
        print(f"  ⚠️ geocode '{address}': {e}")
        cache[address] = None
        return None

def enrich_geocoding(cache, geo_cache):
    total = len(cache)
    done  = sum(1 for l in cache.values() if l.get("lat") is not None)
    todo  = [l for l in cache.values() if l.get("lat") is None]

    print(f"\n🧭 Géocodage : {done} déjà faits, {len(todo)} à faire")

    for i, listing in enumerate(todo, 1):
        coords = geocode(listing.get("adresse_brute",""), geo_cache)
        if coords:
            listing["lat"], listing["lon"] = coords

        if i % 50 == 0:
            print(f"  {i}/{len(todo)}...")
            save_json(GEOCODE_CACHE, geo_cache)
            save_json(CACHE_PATH, cache)

    save_json(GEOCODE_CACHE, geo_cache)
    save_json(CACHE_PATH, cache)
    geocoded = sum(1 for l in cache.values() if l.get("lat") is not None)
    print(f"  ✅ {geocoded}/{total} géocodés")

# ── Attribution polygones ─────────────────────────────────────────────────────

def assign_polygons(cache):
    try:
        from geo_assigner import GeoAssigner
        assigner = GeoAssigner()
    except FileNotFoundError as e:
        print(f"\n⚠️  {e}")
        print("  Lance d'abord : python download_geojson.py")
        return

    print("\n🗺️  Attribution polygones...")
    matched = 0
    for listing in cache.values():
        result = assigner.assign_quartier(listing.get("lat"), listing.get("lon"))
        if result:
            listing["quartier_polygonal"] = result["quartier"]
            listing["ville_polygonale"]   = result["ville"]
            matched += 1
        else:
            listing["quartier_polygonal"] = None
            listing["ville_polygonale"]   = None

    print(f"  ✅ {matched}/{len(cache)} matchés")

# ── Agrégation intelligente ───────────────────────────────────────────────────

def aggregate(cache):
    """
    Agrégation par quartier polygonal.
    - Médiane robuste (ignore les outliers)
    - Percentiles P25/P75 pour mieux représenter la distribution
    - Moyenne par type de logement
    """
    from statistics import median, mean, stdev

    quartiers = {}

    for listing in cache.values():
        q   = listing.get("quartier_polygonal")
        p   = listing.get("prix")
        lat = listing.get("lat")
        lon = listing.get("lon")
        t   = listing.get("type_logement")

        if not q or not p or lat is None or lon is None:
            continue

        if q not in quartiers:
            quartiers[q] = {
                "prix": [], "coords": [],
                "ville": listing.get("ville_polygonale"),
                "types": Counter(),
            }

        quartiers[q]["prix"].append(p)
        quartiers[q]["coords"].append((lat, lon))
        if t:
            quartiers[q]["types"][t] += 1

    result = {}

    for q, data in quartiers.items():
        prix   = sorted(data["prix"])
        coords = data["coords"]
        n      = len(prix)

        if n < 2:
            continue

        # Percentiles
        p25 = prix[max(0, int(n * 0.25))]
        p75 = prix[min(n-1, int(n * 0.75))]

        # Médiane robuste (retire les 10% extrêmes si assez de données)
        if n >= 10:
            trim = max(1, int(n * 0.1))
            prix_trim = prix[trim:-trim]
        else:
            prix_trim = prix

        avg_lat = sum(c[0] for c in coords) / n
        avg_lon = sum(c[1] for c in coords) / n

        # Moyenne par type
        par_type = {}
        for listing in cache.values():
            if listing.get("quartier_polygonal") != q: continue
            t = listing.get("type_logement")
            p2 = listing.get("prix")
            if t and p2:
                par_type.setdefault(t, []).append(p2)
        moyennes_par_type = {t: round(median(ps)) for t, ps in par_type.items() if ps}

        result[q] = {
            "loyer_median":       round(median(prix_trim)),
            "loyer_moyen":        round(mean(prix_trim)),
            "loyer_p25":          round(p25),
            "loyer_p75":          round(p75),
            "loyer_min":          min(prix),
            "loyer_max":          max(prix),
            "nb_annonces":        n,
            "ecart_type":         round(stdev(prix)) if n > 1 else 0,
            "lat":                round(avg_lat, 6),
            "lon":                round(avg_lon, 6),
            "ville":              data["ville"],
            "types":              dict(data["types"]),
            "loyer_par_type":     moyennes_par_type,
        }

    return dict(sorted(result.items(), key=lambda x: x[1]["nb_annonces"], reverse=True))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🏠 Scraper Kijiji v2 — RMR Montréal complète")
    print("=" * 50)

    cache     = load_json(CACHE_PATH)
    geo_cache = load_json(GEOCODE_CACHE)
    print(f"📦 Cache : {len(cache)} annonces existantes")

    session   = requests.Session()
    total_new = 0

    for name, path in REGIONS.items():
        new = scrape_region(name, path, cache, session)
        total_new += new
        save_json(CACHE_PATH, cache)

    print(f"\n✅ {total_new} nouvelles annonces | Total : {len(cache)}")

    enrich_geocoding(cache, geo_cache)
    assign_polygons(cache)
    save_json(CACHE_PATH, cache)

    print("\n🔢 Agrégation...")
    result = aggregate(cache)
    save_json(OUTPUT_PATH, result)

    print(f"  {len(result)} quartiers → {OUTPUT_PATH}")
    print("\n📋 Top 15 :")
    for i, (q, s) in enumerate(list(result.items())[:15], 1):
        types_str = " | ".join(f"{k}:{v}" for k,v in sorted(s.get("types",{}).items()))
        print(f"  {i:2}. {q:<40} {s['nb_annonces']:>3} ann.  "
              f"~{s['loyer_median']}$ (P25:{s['loyer_p25']} P75:{s['loyer_p75']})")

if __name__ == "__main__":
    main()
