import json
from pathlib import Path

from shapely.geometry import Point, shape


BASE_DIR = Path(__file__).parent
GEOJSON_PATH = BASE_DIR / "data" / "quartiers_rmr.geojson.json"

class GeoAssigner:
    def __init__(self, geojson_path: Path = GEOJSON_PATH):
        self.geojson_path = geojson_path
        self.features = self._load_features()

    def _load_features(self) -> list[dict]:
        if not self.geojson_path.exists():
            raise FileNotFoundError(
                f"GeoJSON introuvable : {self.geojson_path}. "
                f"Ajoute data/quartiers_rmr.geojson"
            )

        with open(self.geojson_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        features = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            geom = feature.get("geometry")

            if not geom:
                continue

            quartier = (
                props.get("ARRONDISSEMENT")
                or props.get("NOM")
                or props.get("name")
                or props.get("nom")
                or props.get("quartier")
                or props.get("district")
                or "Inconnu"
            )

            ville = (
                props.get("VILLE")
                or props.get("CITY")
                or props.get("ville")
                or props.get("city")
                or "Montréal"
            )

            features.append({
                "quartier": quartier,
                "ville": ville,
                "geometry": shape(geom),
                "properties": props,
            })

        return features

    def assign_quartier(self, lat: float | None, lon: float | None) -> dict | None:
        if lat is None or lon is None:
            return None

        point = Point(lon, lat)

        for feature in self.features:
            geom = feature["geometry"]
            if geom.contains(point) or geom.touches(point):
                return {
                    "quartier": feature["quartier"],
                    "ville": feature["ville"],
                    "properties": feature["properties"],
                }

        return None