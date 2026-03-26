"""
Scraper Kijiji — Loyers RMR Montréal
- scrape plus profond
- garde les annonces géocodées
- détecte le type de logement
- assigne les polygones
- agrège par quartier
"""

import html
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median

import requests
from bs4 import BeautifulSoup

from geo_assigner import GeoAssigner

BASE_URL = "https://www.kijiji.ca"

REGIONS = {
    "montreal": "/b-appartement-condo/ville-de-montreal/c37l1700281",
    "laval": "/b-appartement-condo/laval-rive-nord/c37l1700281",
    "rive_sud": "/b-appartement-condo/rive-sud-montreal/c37l1700281",
    "longueuil": "/b-appartement-condo/longueuil-rive-sud/c37l1700281",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {
    "User-Agent": "projet-logement-rmr/1.0 (contact: antoine@toenz.com)"
}

BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / "data" / "kijiji_cache.json"
OUTPUT_PATH = BASE_DIR / "data" / "loyers_par_quartier.json"
GEOCODE_CACHE_PATH = BASE_DIR / "data" / "geocode_cache.json"

DELAY = 2.0
MAX_PAGES = 40
GEOCODE_DELAY = 1.1
MAX_ZERO_NEW_PAGES = 3


def load_json(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_price(price_str):
    if not price_str:
        return None

    price_str = (
        str(price_str)
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(",", "")
        .replace("$", "")
    )

    m = re.search(r"(\d+)", price_str)
    if not m:
        return None

    val = float(m.group(1))
    if 400 <= val <= 8000:
        return val
    return None


def safe_str(value) -> str:
    if value is None:
        return ""
    s = html.unescape(str(value)).strip()
    if s.lower() in {"none", "null", ""}:
        return ""
    return s


def normalize_type_logement(text: str) -> str | None:
    if not text:
        return None

    t = safe_str(text).lower()
    t = t.replace(",", ".").replace("½", "1/2")

    patterns = [
        (r"\b1\s*[/\.]?\s*1/2\b", "1 1/2"),
        (r"\b1\s*[/\.]?\s*2\b", "1 1/2"),
        (r"\b2\s*[/\.]?\s*1/2\b", "2 1/2"),
        (r"\b2\s*[/\.]?\s*2\b", "2 1/2"),
        (r"\b3\s*[/\.]?\s*1/2\b", "3 1/2"),
        (r"\b3\s*[/\.]?\s*2\b", "3 1/2"),
        (r"\b4\s*[/\.]?\s*1/2\b", "4 1/2"),
        (r"\b4\s*[/\.]?\s*2\b", "4 1/2"),
        (r"\b5\s*[/\.]?\s*1/2\b", "5 1/2"),
        (r"\b5\s*[/\.]?\s*2\b", "5 1/2"),
        (r"\b6\s*[/\.]?\s*1/2\b", "6 1/2"),
        (r"\b6\s*[/\.]?\s*2\b", "6 1/2"),
        (r"\bstudio\b", "1 1/2"),
        (r"\bbachelor\b", "1 1/2"),
        (r"\b1\s*bed(room)?\b", "3 1/2"),
        (r"\bone[- ]bed(room)?\b", "3 1/2"),
        (r"\b2\s*bed(room)?s?\b", "4 1/2"),
        (r"\btwo[- ]bed(room)?s?\b", "4 1/2"),
        (r"\b3\s*bed(room)?s?\b", "5 1/2"),
        (r"\bthree[- ]bed(room)?s?\b", "5 1/2"),
        (r"\b4\s*bed(room)?s?\b", "6 1/2"),
        (r"\bfour[- ]bed(room)?s?\b", "6 1/2"),
    ]

    for pattern, label in patterns:
        if re.search(pattern, t):
            return label

    return None


def build_address_string(
    street_address: str = "",
    locality: str = "",
    region_name: str = "",
    postal_code: str = "",
) -> str:
    parts = [
        safe_str(street_address),
        safe_str(locality),
        safe_str(postal_code),
        "Québec",
        "Canada",
    ]
    result = ", ".join([p for p in parts if p])
    return result or region_name.replace("_", " ").title()


def extract_address_fields_from_schema(item: dict, region_name: str) -> dict:
    addr = item.get("address", {})

    if isinstance(addr, dict):
        street = safe_str(addr.get("streetAddress"))
        locality = safe_str(addr.get("addressLocality"))
        postal_code = safe_str(addr.get("postalCode"))
    else:
        street = safe_str(addr)
        locality = ""
        postal_code = ""

    return {
        "adresse_brute": build_address_string(street, locality, region_name, postal_code),
        "ville_brute": locality or region_name.replace("_", " ").title(),
        "quartier_brut": locality or street or region_name.replace("_", " ").title(),
    }


def extract_address_fields_from_props(ad: dict, region_name: str) -> dict:
    location = ad.get("location") or {}

    street = safe_str(location.get("mapAddress"))
    locality = safe_str(location.get("name") or ad.get("locationName"))
    postal_code = safe_str(location.get("postalCode"))

    return {
        "adresse_brute": build_address_string(street, locality, region_name, postal_code),
        "ville_brute": locality or region_name.replace("_", " ").title(),
        "quartier_brut": locality or street or region_name.replace("_", " ").title(),
    }


def parse_schema_listings(html_text, region_name):
    soup = BeautifulSoup(html_text, "html.parser")
    listings = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        if data.get("@type") != "ItemList":
            continue

        for item_wrap in data.get("itemListElement", []):
            item = item_wrap.get("item", {})
            if not item:
                continue

            offers = item.get("offers", {})
            price = parse_price(offers.get("price") or offers.get("lowPrice"))
            if not price:
                continue

            address_fields = extract_address_fields_from_schema(item, region_name)
            adresse_brute = safe_str(address_fields["adresse_brute"])
            ville_brute = safe_str(address_fields["ville_brute"])
            if not adresse_brute or not ville_brute:
                continue

            titre = safe_str(item.get("name", ""))
            description = safe_str(item.get("description", ""))
            type_logement = normalize_type_logement(f"{titre} {description}")

            url = item.get("url", "")
            listing_id = url.split("/")[-1].split("?")[0] if url else str(hash(titre))

            listings.append({
                "id": listing_id,
                "prix": price,
                "titre": titre,
                "description": description,
                "type_logement": type_logement,
                "url": url,
                "region": region_name,
                "adresse_brute": adresse_brute,
                "ville_brute": ville_brute,
                "quartier_brut": safe_str(address_fields["quartier_brut"]),
                "lat": None,
                "lon": None,
                "quartier_polygonal": None,
                "ville_polygonale": None,
                "scraped_at": datetime.now().isoformat(),
            })

    return listings


def parse_props_listings(html_text, region_name):
    soup = BeautifulSoup(html_text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return []

    try:
        data = json.loads(script.string or "")
    except (json.JSONDecodeError, TypeError):
        return []

    ads = []
    try:
        page_props = data["props"]["pageProps"]
        for key in ["adList", "ads", "listings", "results"]:
            if key in page_props:
                ads = page_props[key]
                break

        if not ads and "initialState" in page_props:
            state = page_props["initialState"]
            for key in ["adList", "ads", "listings"]:
                if key in state:
                    ads = state[key]
                    break
    except (KeyError, TypeError):
        pass

    listings = []

    for ad in ads:
        if not isinstance(ad, dict):
            continue

        price = parse_price((ad.get("price") or {}).get("amount") or ad.get("price"))
        if not price:
            continue

        address_fields = extract_address_fields_from_props(ad, region_name)
        adresse_brute = safe_str(address_fields["adresse_brute"])
        ville_brute = safe_str(address_fields["ville_brute"])
        if not adresse_brute or not ville_brute:
            continue

        titre = safe_str(ad.get("title", ""))
        description = safe_str(ad.get("description", ""))
        type_logement = normalize_type_logement(f"{titre} {description}")

        listing_id = str(ad.get("id") or ad.get("adId") or hash(titre))
        url = ad.get("seoUrl") or ad.get("url", "")
        if url.startswith("/"):
            url = f"{BASE_URL}{url}"

        listings.append({
            "id": listing_id,
            "prix": price,
            "titre": titre,
            "description": description,
            "type_logement": type_logement,
            "url": url,
            "region": region_name,
            "adresse_brute": adresse_brute,
            "ville_brute": ville_brute,
            "quartier_brut": safe_str(address_fields["quartier_brut"]),
            "lat": None,
            "lon": None,
            "quartier_polygonal": None,
            "ville_polygonale": None,
            "scraped_at": datetime.now().isoformat(),
        })

    return listings


def scrape_page(url, session):
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"  ⚠️ Erreur : {e}")
        return None


def scrape_region(region_name, region_path, cache, session):
    print(f"\n📍 Région : {region_name}")
    new_count = 0
    zero_new_pages = 0

    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}{region_path}?page={page}"
        print(f"  Page {page}/{MAX_PAGES}...", end=" ", flush=True)

        html_text = scrape_page(url, session)
        if not html_text:
            print("erreur → arrêt")
            break

        listings = parse_schema_listings(html_text, region_name)
        if not listings:
            listings = parse_props_listings(html_text, region_name)

        if not listings:
            print("vide → arrêt")
            break

        added = 0
        for listing in listings:
            if listing["id"] not in cache:
                cache[listing["id"]] = listing
                added += 1

        new_count += added
        print(f"{len(listings)} annonces ({added} nouvelles)")

        if added == 0:
            zero_new_pages += 1
        else:
            zero_new_pages = 0

        if page > 5 and zero_new_pages >= MAX_ZERO_NEW_PAGES:
            print("  → Plusieurs pages sans nouveautés, arrêt")
            break

        time.sleep(DELAY)

    return new_count


def geocode_address(address: str, geocode_cache: dict) -> tuple[float, float] | None:
    if not address:
        return None

    if address in geocode_cache:
        cached = geocode_cache[address]
        return tuple(cached) if cached else None

    try:
        resp = requests.get(
            f"{NOMINATIM_BASE}/search",
            params={"q": address, "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()

        if not results:
            geocode_cache[address] = None
            return None

        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        geocode_cache[address] = [lat, lon]
        time.sleep(GEOCODE_DELAY)
        return lat, lon

    except Exception as e:
        print(f"  ⚠️ Géocodage échoué pour '{address}': {e}")
        geocode_cache[address] = None
        return None


def enrich_cache_with_geocoding(cache: dict, geocode_cache: dict):
    total = len(cache)
    done = 0

    print("\n🧭 Géocodage des annonces...")
    for listing in cache.values():
        done += 1

        if listing.get("lat") is not None and listing.get("lon") is not None:
            continue

        coords = geocode_address(listing.get("adresse_brute", ""), geocode_cache)
        if coords:
            listing["lat"], listing["lon"] = coords

        if done % 25 == 0:
            print(f"  {done}/{total} annonces traitées")
            save_json(GEOCODE_CACHE_PATH, geocode_cache)
            save_json(CACHE_PATH, cache)

    save_json(GEOCODE_CACHE_PATH, geocode_cache)
    save_json(CACHE_PATH, cache)


def assign_polygons(cache: dict):
    print("\n🗺️ Attribution aux quartiers polygonaux...")
    assigner = GeoAssigner()

    matched = 0
    unmatched = 0
    stats_by_region = {}

    for listing in cache.values():
        region = listing.get("region", "inconnue")
        stats_by_region.setdefault(region, {"matched": 0, "unmatched": 0})

        result = assigner.assign_quartier(listing.get("lat"), listing.get("lon"))

        if result:
            listing["quartier_polygonal"] = result["quartier"]
            listing["ville_polygonale"] = result["ville"]
            matched += 1
            stats_by_region[region]["matched"] += 1
        else:
            listing["quartier_polygonal"] = None
            listing["ville_polygonale"] = None
            unmatched += 1
            stats_by_region[region]["unmatched"] += 1

    print(f"  ✅ Matchés : {matched}")
    print(f"  ⚠️ Hors polygones / introuvables : {unmatched}")

    print("\n  Détail par région :")
    for region, stats in stats_by_region.items():
        total = stats["matched"] + stats["unmatched"]
        print(
            f"  - {region:<10} {stats['matched']:>3} matchés / "
            f"{stats['unmatched']:>3} hors polygones (total {total})"
        )


def aggregate(cache):
    quartiers = {}

    for listing in cache.values():
        q = listing.get("quartier_polygonal")
        p = listing.get("prix")
        lat = listing.get("lat")
        lon = listing.get("lon")
        t = listing.get("type_logement")

        if not q or not p or lat is None or lon is None:
            continue

        quartiers.setdefault(q, {
            "prix": [],
            "coords": [],
            "ville": listing.get("ville_polygonale"),
            "types_counter": Counter(),
        })

        quartiers[q]["prix"].append(p)
        quartiers[q]["coords"].append((lat, lon))

        if t:
            quartiers[q]["types_counter"][t] += 1

    result = {}
    for q, data in quartiers.items():
        prix = data["prix"]
        coords = data["coords"]

        if len(prix) < 2:
            continue

        avg_lat = sum(c[0] for c in coords) / len(coords)
        avg_lon = sum(c[1] for c in coords) / len(coords)

        result[q] = {
            "loyer_median": round(median(prix)),
            "loyer_moyen": round(mean(prix)),
            "loyer_min": min(prix),
            "loyer_max": max(prix),
            "nb_annonces": len(prix),
            "lat": round(avg_lat, 6),
            "lon": round(avg_lon, 6),
            "ville": data["ville"],
            "types": dict(data["types_counter"]),
        }

    return dict(sorted(result.items(), key=lambda x: x[1]["nb_annonces"], reverse=True))


def main():
    print("🏠 Scraper Kijiji — Loyers RMR Montréal")
    print("=" * 45)

    cache = load_json(CACHE_PATH)
    geocode_cache = load_json(GEOCODE_CACHE_PATH)

    print(f"📦 Cache existant : {len(cache)} annonces")

    session = requests.Session()
    total_new = 0

    for region_name, region_path in REGIONS.items():
        new = scrape_region(region_name, region_path, cache, session)
        total_new += new
        save_json(CACHE_PATH, cache)

    print(f"\n✅ Terminé : {total_new} nouvelles annonces")
    print(f"📊 Total : {len(cache)} annonces")

    enrich_cache_with_geocoding(cache, geocode_cache)
    assign_polygons(cache)
    save_json(CACHE_PATH, cache)

    print("\n🔢 Agrégation par quartier polygonal...")
    result = aggregate(cache)
    save_json(OUTPUT_PATH, result)

    print(f"  {len(result)} quartiers")
    print(f"  💾 → {OUTPUT_PATH}")

    print("\n📋 Top 10 quartiers :")
    for i, (q, s) in enumerate(list(result.items())[:10], 1):
        print(f"{i:>4}. {q:<35} {s['nb_annonces']:>3} annonces  ~{s['loyer_median']}$ / mois")


if __name__ == "__main__":
    main()