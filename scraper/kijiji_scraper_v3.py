"""
Scraper Kijiji v3 — par type de logement × région
6 types × 6 régions = 36 URLs = ~1500 annonces uniques
Cache delta : ne re-scrape jamais ce qui existe déjà
"""

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev
from collections import Counter

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.kijiji.ca"

# ── 36 combinaisons type × région ────────────────────────────────────────────

REGION_SLUGS = {
    "montreal":  "ville-de-montreal",
    "laval":     "laval-rive-nord",
    "longueuil": "longueuil-rive-sud",
    "rive_sud":  "rive-sud-montreal",
    "rive_nord": "rive-nord",
    "ouest_ile": "ouest-de-l-ile",
}

TYPE_SLUGS = {
    "1 1/2": "1-chambre-et-demi",
    "2 1/2": "2-chambres-et-demi",
    "3 1/2": "3-chambres-et-demi",
    "4 1/2": "4-chambres-et-demi",
    "5 1/2": "5-chambres-et-demi",
    "6 1/2": "6-chambres-et-demi",
}

CATEGORY = "c37l1700281"

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

BASE_DIR      = Path(__file__).resolve().parent
CACHE_PATH    = BASE_DIR / "data" / "kijiji_cache.json"
OUTPUT_PATH   = BASE_DIR / "data" / "loyers_par_quartier.json"
GEOCODE_CACHE = BASE_DIR / "data" / "geocode_cache.json"

DELAY      = 2.5   # secondes entre pages
MAX_PAGES  = 10    # par combinaison type×région

NOMINATIM_BASE    = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {"User-Agent": "projet-logement-rmr/1.0"}
GEOCODE_DELAY     = 1.1

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path):
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
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
    s = re.sub(r"[^\d]", "", str(val))
    if not s: return None
    v = float(s)
    return v if 400 <= v <= 8000 else None

# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_schema(html_text, region, type_logement):
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
            if isinstance(addr, dict):
                locality = safe(addr.get("addressLocality",""))
                street   = safe(addr.get("streetAddress",""))
                postal   = safe(addr.get("postalCode",""))
            else:
                locality = safe(addr)
                street = postal = ""

            adresse_brute = ", ".join(filter(None, [street, locality, postal, "Québec", "Canada"]))
            if not adresse_brute:
                adresse_brute = f"{region.replace('_',' ').title()}, Québec, Canada"

            url = item.get("url","")
            lid = url.split("/")[-1].split("?")[0] if url else str(hash(safe(item.get("name",""))))

            listings.append({
                "id": lid,
                "prix": price,
                "titre": safe(item.get("name","")),
                "type_logement": type_logement,
                "url": url,
                "region": region,
                "adresse_brute": adresse_brute,
                "ville_brute": locality or region.replace("_"," ").title(),
                "quartier_brut": locality or street or region.replace("_"," ").title(),
                "lat": None, "lon": None,
                "quartier_polygonal": None, "ville_polygonale": None,
                "scraped_at": datetime.now().isoformat(),
            })

    return listings

def parse_next_data(html_text, region, type_logement):
    soup = BeautifulSoup(html_text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script: return []

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
            for key in ["adList","ads","listings"]:
                if key in pp["initialState"]:
                    ads = pp["initialState"][key]; break
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
            adresse_brute = f"{region.replace('_',' ').title()}, Québec, Canada"

        url = ad.get("seoUrl") or ad.get("url","")
        if url.startswith("/"): url = BASE_URL + url
        lid = str(ad.get("id") or ad.get("adId") or hash(safe(ad.get("title",""))))

        listings.append({
            "id": lid,
            "prix": price,
            "titre": safe(ad.get("title","")),
            "type_logement": type_logement,
            "url": url,
            "region": region,
            "adresse_brute": adresse_brute,
            "ville_brute": locality or region.replace("_"," ").title(),
            "quartier_brut": locality or street or region.replace("_"," ").title(),
            "lat": None, "lon": None,
            "quartier_polygonal": None, "ville_polygonale": None,
            "scraped_at": datetime.now().isoformat(),
        })

    return listings

# ── Scraping ──────────────────────────────────────────────────────────────────

def scrape_url(url, session):
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"⚠️ {e}")
        return None

def scrape_combination(region, type_logement, type_slug, region_slug, cache, session):
    new_count = 0
    url_base = f"{BASE_URL}/b-{type_slug}/{region_slug}/{CATEGORY}"

    for page in range(1, MAX_PAGES + 1):
        url = f"{url_base}?page={page}" if page > 1 else url_base
        html_text = scrape_url(url, session)
        if not html_text:
            break

        listings = parse_schema(html_text, region, type_logement)
        if not listings:
            listings = parse_next_data(html_text, region, type_logement)
        if not listings:
            break

        added = 0
        for l in listings:
            if l["id"] not in cache:
                cache[l["id"]] = l
                added += 1

        new_count += added
        if added == 0 and page > 1:
            break

        time.sleep(DELAY)

    return new_count

# ── Géocodage ─────────────────────────────────────────────────────────────────

def geocode(address, geo_cache):
    if not address: return None
    if address in geo_cache:
        return tuple(geo_cache[address]) if geo_cache[address] else None
    try:
        r = requests.get(
            f"{NOMINATIM_BASE}/search",
            params={"q": address, "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS, timeout=15,
        )
        results = r.json()
        if results:
            lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
            geo_cache[address] = [lat, lon]
            time.sleep(GEOCODE_DELAY)
            return lat, lon
        geo_cache[address] = None
        return None
    except Exception as e:
        print(f"⚠️ geocode: {e}")
        geo_cache[address] = None
        return None

def enrich_geocoding(cache, geo_cache):
    todo = [l for l in cache.values() if l.get("lat") is None]
    done_before = len(cache) - len(todo)
    print(f"\n🧭 Géocodage : {done_before} déjà faits, {len(todo)} à faire...")

    for i, listing in enumerate(todo, 1):
        coords = geocode(listing.get("adresse_brute",""), geo_cache)
        if coords:
            listing["lat"], listing["lon"] = coords
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}...")
            save_json(GEOCODE_CACHE, geo_cache)
            save_json(CACHE_PATH, cache)

    save_json(GEOCODE_CACHE, geo_cache)
    save_json(CACHE_PATH, cache)
    geocoded = sum(1 for l in cache.values() if l.get("lat") is not None)
    print(f"  ✅ {geocoded}/{len(cache)} géocodés")

# ── Attribution polygones ─────────────────────────────────────────────────────

def assign_polygons(cache):
    try:
        from geo_assigner import GeoAssigner
        assigner = GeoAssigner()
    except FileNotFoundError as e:
        print(f"\n⚠️ {e}")
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
                "prix_par_type": {},
            }

        quartiers[q]["prix"].append(p)
        quartiers[q]["coords"].append((lat, lon))
        if t:
            quartiers[q]["types"][t] += 1
            quartiers[q]["prix_par_type"].setdefault(t, []).append(p)

    result = {}
    for q, data in quartiers.items():
        prix = sorted(data["prix"])
        n    = len(prix)
        if n < 2: continue

        # Médiane tronquée (retire 10% extrêmes si assez de données)
        if n >= 10:
            trim = max(1, int(n * 0.1))
            prix_trim = prix[trim:-trim]
        else:
            prix_trim = prix

        p25 = prix[max(0, int(n * 0.25))]
        p75 = prix[min(n-1, int(n * 0.75))]

        coords = data["coords"]
        avg_lat = sum(c[0] for c in coords) / n
        avg_lon = sum(c[1] for c in coords) / n

        # Médiane par type
        loyer_par_type = {
            t: round(median(ps))
            for t, ps in data["prix_par_type"].items()
            if len(ps) >= 2
        }

        result[q] = {
            "loyer_median":   round(median(prix_trim)),
            "loyer_moyen":    round(mean(prix_trim)),
            "loyer_p25":      round(p25),
            "loyer_p75":      round(p75),
            "loyer_min":      min(prix),
            "loyer_max":      max(prix),
            "ecart_type":     round(stdev(prix)) if n > 1 else 0,
            "nb_annonces":    n,
            "lat":            round(avg_lat, 6),
            "lon":            round(avg_lon, 6),
            "ville":          data["ville"],
            "types":          dict(data["types"]),
            "loyer_par_type": loyer_par_type,
        }

    return dict(sorted(result.items(), key=lambda x: x[1]["nb_annonces"], reverse=True))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🏠 Scraper Kijiji v3 — par type × région")
    print("=" * 50)

    cache     = load_json(CACHE_PATH)
    geo_cache = load_json(GEOCODE_CACHE)
    print(f"📦 Cache existant : {len(cache)} annonces")

    session   = requests.Session()
    total_new = 0
    combos    = [(r, t, ts, rs) for r, rs in REGION_SLUGS.items()
                                for t, ts in TYPE_SLUGS.items()]

    print(f"🔄 {len(combos)} combinaisons à scraper\n")

    for i, (region, type_logement, type_slug, region_slug) in enumerate(combos, 1):
        label = f"{type_logement} / {region}"
        print(f"[{i:2}/{len(combos)}] {label:<30}", end=" ", flush=True)

        new = scrape_combination(region, type_logement, type_slug, region_slug, cache, session)
        total_new += new
        print(f"+{new} nouvelles (total cache: {len(cache)})")

        # Sauvegarde tous les 6 combos (1 région complète)
        if i % 6 == 0:
            save_json(CACHE_PATH, cache)

    save_json(CACHE_PATH, cache)
    print(f"\n✅ {total_new} nouvelles annonces | Total : {len(cache)}")

    enrich_geocoding(cache, geo_cache)
    assign_polygons(cache)
    save_json(CACHE_PATH, cache)

    print("\n🔢 Agrégation intelligente...")
    result = aggregate(cache)
    save_json(OUTPUT_PATH, result)

    print(f"\n  {len(result)} quartiers → {OUTPUT_PATH}")
    print("\n📋 Top 15 :")
    for i, (q, s) in enumerate(list(result.items())[:15], 1):
        types_str = " | ".join(f"{k}:{v}" for k,v in sorted(s.get("types",{}).items()))
        print(f"  {i:2}. {q:<42} {s['nb_annonces']:>4} ann. "
              f"~{s['loyer_median']}$ "
              f"[P25:{s['loyer_p25']} P75:{s['loyer_p75']}]")
        if s.get("loyer_par_type"):
            par_type = " | ".join(f"{k}:{v}$" for k,v in sorted(s["loyer_par_type"].items()))
            print(f"      {par_type}")

if __name__ == "__main__":
    main()
