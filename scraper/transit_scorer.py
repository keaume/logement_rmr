"""
Calcul des temps de trajet + score logement
Version simple et stable :
- pas de dépendance obligatoire à ORS/Navitia
- fallback local systématique
- bonus "proximité métro" simple
"""

import json
import time
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
TRANSIT_CACHE_PATH = BASE_DIR / "data" / "transit_cache.json"
GEOCODE_CACHE_PATH = BASE_DIR / "data" / "geocode_cache.json"

# ── APIs / config ─────────────────────────────────────────────────────────────

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {
    "User-Agent": "loyer-rmr-montreal/1.0 (projet-logement)"
}

# Facultatif : laisse vide si tu n'as pas de compte
NAVITIA_TOKEN = ""

# Quelques stations de métro / hubs STM-REM pour un bonus simple
METRO_POINTS = [
    (45.5152, -73.5610),  # Berri-UQAM
    (45.5248, -73.5817),  # Mont-Royal
    (45.4901, -73.5803),  # Atwater
    (45.4995, -73.5740),  # Peel
    (45.5033, -73.5690),  # McGill
    (45.5067, -73.5594),  # Champ-de-Mars
    (45.5084, -73.5542),  # Beaudry
    (45.4924, -73.5875),  # Lionel-Groulx
    (45.4738, -73.5994),  # Vendôme
    (45.5101, -73.6830),  # Côte-Vertu
    (45.5312, -73.6226),  # Snowdon
    (45.5590, -73.6199),  # Henri-Bourassa
    (45.4448, -73.6039),  # Longueuil–Université-de-Sherbrooke
    (45.5582, -73.5518),  # Pie-IX
    (45.5518, -73.7092),  # Montmorency
]

# ── Cache helpers ─────────────────────────────────────────────────────────────

def load_json_cache(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json_cache(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Distance / fallback ───────────────────────────────────────────────────────

def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return r * c

def estimate_fallback_time(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> int:
    """
    Estimation simple du trajet en minutes basée sur la distance.
    Pour une V1, c'est largement suffisant si aucune API TC n'est configurée.
    """
    dist = distance_km(origin_lat, origin_lon, dest_lat, dest_lon)

    # Approx urbaine conservatrice Montréal
    # ~ 4.5 min par km + minimum 10 min
    return max(10, round(dist * 4.5))

def metro_score(lat: float, lon: float) -> int:
    """
    Bonus simple si le quartier est proche d'une station/hub.
    """
    min_dist = min(distance_km(lat, lon, mlat, mlon) for mlat, mlon in METRO_POINTS)

    if min_dist < 0.8:
        return 10
    if min_dist < 1.5:
        return 5
    return 0

# ── Géocodage ─────────────────────────────────────────────────────────────────

def geocode_address(address: str, cache: dict) -> tuple[float, float] | None:
    """
    Géocode une adresse via Nominatim.
    Retourne (lat, lon) ou None.
    """
    if not address:
        return None

    if address in cache:
        return tuple(cache[address]) if cache[address] else None

    try:
        resp = requests.get(
            f"{NOMINATIM_BASE}/search",
            params={"q": address, "format": "json", "limit": 1},
            headers=NOMINATIM_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()

        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            cache[address] = [lat, lon]
            save_json_cache(GEOCODE_CACHE_PATH, cache)
            time.sleep(1.1)  # respect Nominatim
            return lat, lon

        cache[address] = None
        save_json_cache(GEOCODE_CACHE_PATH, cache)
        return None

    except Exception as e:
        print(f"⚠️ Géocodage échoué pour '{address}': {e}")
        cache[address] = None
        save_json_cache(GEOCODE_CACHE_PATH, cache)
        return None

def geocode_neighbourhood(neighbourhood: str, cache: dict) -> tuple[float, float] | None:
    """
    Géocode un quartier/secteur de la RMR.
    """
    suffixes = [
        f"{neighbourhood}, Montréal, Québec, Canada",
        f"{neighbourhood}, Québec, Canada",
        neighbourhood,
    ]

    for query in suffixes:
        result = geocode_address(query, cache)
        if result:
            return result
    return None

# ── Trajet réel (facultatif) ─────────────────────────────────────────────────

def get_transit_time_navitia(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> int | None:
    """
    Si NAVITIA_TOKEN est vide, retourne directement None.
    Sinon tente un vrai calcul TC.
    """
    if not NAVITIA_TOKEN:
        return None

    cache = load_json_cache(TRANSIT_CACHE_PATH)
    cache_key = f"nav:{origin_lat:.4f},{origin_lon:.4f}->{dest_lat:.4f},{dest_lon:.4f}"

    if cache_key in cache:
        return cache[cache_key]

    try:
        resp = requests.get(
            "https://api.navitia.io/v1/coverage/ca-montréal/journeys",
            auth=(NAVITIA_TOKEN, ""),
            params={
                "from": f"{origin_lon};{origin_lat}",
                "to": f"{dest_lon};{dest_lat}",
                "count": 1,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        journeys = data.get("journeys", [])
        if not journeys:
            return None

        duration_sec = journeys[0].get("duration")
        if duration_sec is None:
            return None

        minutes = round(duration_sec / 60)
        cache[cache_key] = minutes
        save_json_cache(TRANSIT_CACHE_PATH, cache)
        return minutes

    except Exception as e:
        print(f"⚠️ Navitia erreur : {e}")
        return None

# ── Scoring ───────────────────────────────────────────────────────────────────

def compute_affordability_score(
    loyer_median: float,
    salaire_annuel: float,
    temps_trajet_min: int | None,
    metro_bonus: int = 0,
    max_trajet_min: int = 45,
    ratio_max: float = 0.33,
    poids_transport: float = 0.4,  # 👈 AJOUT
) -> dict:

    revenu_mensuel_brut = salaire_annuel / 12
    revenu_mensuel_net = revenu_mensuel_brut * 0.75

    ratio_loyer = loyer_median / revenu_mensuel_net if revenu_mensuel_net > 0 else 1.0

    score_loyer = max(0, 100 - (ratio_loyer / ratio_max) * 50)

    accessible = True
    if temps_trajet_min is None:
        score_trajet = 70
    else:
        if temps_trajet_min > max_trajet_min:
            accessible = False
            score_trajet = max(0, 100 - (temps_trajet_min - max_trajet_min) * 3)
        else:
            score_trajet = min(100, 100 - max(0, temps_trajet_min - 20) * 1.5)

    # 🔥 NOUVEAU MIX DYNAMIQUE
    poids_loyer = 1 - poids_transport

    score_final = (
        score_loyer * poids_loyer +
        score_trajet * poids_transport +
        metro_bonus
    )

    score_final = min(100, max(0, score_final))

    if score_final >= 65:
        couleur = "vert"
    elif score_final >= 40:
        couleur = "orange"
    else:
        couleur = "rouge"

    return {
        "score": round(score_final, 1),
        "couleur": couleur,
        "ratio_loyer": round(ratio_loyer * 100, 1),
        "revenu_mensuel_net": round(revenu_mensuel_net),
        "loyer_median": loyer_median,
        "temps_trajet_min": temps_trajet_min,
        "metro_bonus": metro_bonus,
        "accessible_trajet": accessible,
    }

# ── Calcul global ─────────────────────────────────────────────────────────────

def compute_all_scores(
    quartiers: dict,
    workplace_address: str,
    salaire_annuel: float,
    max_trajet_min: int = 45,
    ratio_max: float = 0.33,
    poids_transport: float = 0.4,   # 👈 AJOUT
) -> dict:
    """
    Calcule les scores pour tous les quartiers.
    """
    geocode_cache = load_json_cache(GEOCODE_CACHE_PATH)

    print(f"📍 Géocodage lieu de travail : {workplace_address}")
    workplace_coords = geocode_address(workplace_address, geocode_cache)

    if not workplace_coords:
        raise ValueError(f"Impossible de géocoder : {workplace_address}")

    wp_lat, wp_lon = workplace_coords
    print(f"✅ {wp_lat:.4f}, {wp_lon:.4f}")

    results = {}
    total = len(quartiers)

    for i, (quartier, stats) in enumerate(quartiers.items(), start=1):
        print(f"[{i}/{total}] {quartier}...", end=" ", flush=True)

        coords = geocode_neighbourhood(quartier, geocode_cache)
        if not coords:
            print("❌ géocodage échoué")
            continue

        q_lat, q_lon = coords

        trajet = get_transit_time_navitia(q_lat, q_lon, wp_lat, wp_lon)
        if trajet is None:
            trajet = estimate_fallback_time(q_lat, q_lon, wp_lat, wp_lon)

        bonus_metro = metro_score(q_lat, q_lon)

        score_data = compute_affordability_score(
            loyer_median=stats["loyer_median"],
            salaire_annuel=salaire_annuel,
            temps_trajet_min=trajet,
            metro_bonus=bonus_metro,
            max_trajet_min=max_trajet_min,
            ratio_max=ratio_max,
            poids_transport=poids_transport,  
        )

        results[quartier] = {
            **stats,
            **score_data,
            "lat": q_lat,
            "lon": q_lon,
        }

        print(
            f"🏠 {stats['loyer_median']:.0f}$/m "
            f"🚌 {trajet}min "
            f"🚇 +{bonus_metro} "
            f"→ {score_data['couleur'].upper()}"
        )

    return results

# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_quartiers = {
        "Le Plateau-Mont-Royal": {"loyer_median": 1350, "loyer_moyen": 1500, "nb_annonces": 7},
        "Saint-Laurent": {"loyer_median": 1395, "loyer_moyen": 1500, "nb_annonces": 13},
        "Ville-Marie": {"loyer_median": 1650, "loyer_moyen": 1750, "nb_annonces": 57},
    }

    scores = compute_all_scores(
        quartiers=test_quartiers,
        workplace_address="1000 rue De La Gauchetière, Montréal",
        salaire_annuel=88000,
        max_trajet_min=45,
        ratio_max=0.33,
    )

    print("\n📊 Résultats :")
    for q, s in sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True):
        print(
            f"{s['couleur'].upper():<6} "
            f"{q:<30} "
            f"score={s['score']} "
            f"loyer={s['loyer_median']}$ "
            f"trajet={s['temps_trajet_min']}min"
        )