"""
Télécharge les polygones GeoJSON de toute la RMR Montréal :
- 19 arrondissements de Montréal
- Laval (secteurs)
- Longueuil, Brossard, Saint-Lambert, Boucherville (rive-sud)
- Villes de banlieue ouest (Kirkland, Dollard, etc.)

Source : Overpass API (OpenStreetMap) — 100% gratuit
Lance ce script UNE SEULE FOIS pour générer quartiers_rmr.geojson.json
"""

import json
import time
import requests
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent / "data" / "quartiers_rmr.geojson.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "projet-logement-rmr/1.0 (contact: logement-mtl)"}

# ── Liste complète des quartiers/villes RMR ───────────────────────────────────

RMR_PLACES = [
    # Arrondissements Montréal
    "Ahuntsic-Cartierville, Montréal",
    "Anjou, Montréal",
    "Côte-des-Neiges–Notre-Dame-de-Grâce, Montréal",
    "Lachine, Montréal",
    "LaSalle, Montréal",
    "Le Plateau-Mont-Royal, Montréal",
    "Le Sud-Ouest, Montréal",
    "L'Île-Bizard–Sainte-Geneviève, Montréal",
    "Mercier–Hochelaga-Maisonneuve, Montréal",
    "Montréal-Nord, Montréal",
    "Outremont, Montréal",
    "Pierrefonds-Roxboro, Montréal",
    "Rivière-des-Prairies–Pointe-aux-Trembles, Montréal",
    "Rosemont–La Petite-Patrie, Montréal",
    "Saint-Laurent, Montréal",
    "Saint-Léonard, Montréal",
    "Verdun, Montréal",
    "Ville-Marie, Montréal",
    "Villeray–Saint-Michel–Parc-Extension, Montréal",
    "Côte-Saint-Luc, Québec",
    "Mont-Royal, Québec",
    "Westmount, Québec",
    # Laval
    "Chomedey, Laval",
    "Sainte-Rose, Laval",
    "Vimont, Laval",
    "Auteuil, Laval",
    "Laval-des-Rapides, Laval",
    "Pont-Viau, Laval",
    "Renaud, Laval",
    "Duvernay, Laval",
    "Saint-François, Laval",
    "Saint-Vincent-de-Paul, Laval",
    "Fabreville, Laval",
    "Sainte-Dorothée, Laval",
    "Îles-Laval, Laval",
    # Rive-sud
    "Longueuil, Québec",
    "Brossard, Québec",
    "Saint-Lambert, Québec",
    "Boucherville, Québec",
    "Saint-Bruno-de-Montarville, Québec",
    "Greenfield Park, Longueuil",
    "Saint-Hubert, Longueuil",
    # Banlieues ouest
    "Dollard-des-Ormeaux, Québec",
    "Kirkland, Québec",
    "Beaconsfield, Québec",
    "Pointe-Claire, Québec",
    "Dorval, Québec",
    "Vaudreuil-Dorion, Québec",
    # Nord
    "Terrebonne, Québec",
    "Repentigny, Québec",
    "Mascouche, Québec",
    "Blainville, Québec",
    "Mirabel, Québec",
]

def fetch_polygon(place_name: str) -> dict | None:
    """Récupère le polygone d'un lieu via Nominatim."""
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={
                "q": place_name,
                "format": "geojson",
                "polygon_geojson": 1,
                "limit": 1,
                "countrycodes": "ca",
            },
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        if not features:
            return None

        feature = features[0]
        geom = feature.get("geometry", {})

        # On veut un vrai polygone, pas juste un point
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            return None

        # Nom propre
        display_name = feature.get("properties", {}).get("display_name", place_name)
        nom_court = place_name.split(",")[0].strip()

        return {
            "type": "Feature",
            "properties": {
                "NOM": nom_court,
                "NOM_COMPLET": display_name,
                "REGION": place_name.split(",")[-1].strip() if "," in place_name else "Québec",
            },
            "geometry": geom,
        }

    except Exception as e:
        print(f"  ⚠️  Erreur pour '{place_name}': {e}")
        return None


def main():
    print("🗺️  Téléchargement des polygones RMR Montréal")
    print("=" * 50)
    print(f"  {len(RMR_PLACES)} lieux à télécharger")
    print("  (Nominatim : max 1 req/sec — prévoir ~1 minute)\n")

    features = []
    failed = []

    for i, place in enumerate(RMR_PLACES, 1):
        print(f"  [{i:2}/{len(RMR_PLACES)}] {place}...", end=" ", flush=True)
        feature = fetch_polygon(place)

        if feature:
            features.append(feature)
            print("✅")
        else:
            failed.append(place)
            print("❌")

        time.sleep(1.2)  # Respecte le rate limit Nominatim

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    print(f"\n✅ {len(features)} polygones sauvegardés → {OUTPUT_PATH}")

    if failed:
        print(f"\n⚠️  {len(failed)} échecs :")
        for p in failed:
            print(f"  - {p}")


if __name__ == "__main__":
    main()
