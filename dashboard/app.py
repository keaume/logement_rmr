"""
Dashboard — Carte Loyer vs Pouvoir d'achat RMR Montréal
Modes :
- Quartiers
- Fondu (heatmap)
"""

import json
import sys
import unicodedata
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import HeatMap
from streamlit_folium import st_folium

BASE_DIR = Path(__file__).resolve().parents[1]
SCRAPER_DIR = BASE_DIR / "scraper"
sys.path.append(str(SCRAPER_DIR))

from transit_scorer import (
    GEOCODE_CACHE_PATH,
    compute_all_scores,
    geocode_address,
    load_json_cache,
)

LOYERS_PATH = SCRAPER_DIR / "data" / "loyers_par_quartier.json"
GEOJSON_PATH = SCRAPER_DIR / "data" / "quartiers_rmr.geojson.json"
CACHE_PATH = SCRAPER_DIR / "data" / "kijiji_cache.json"

st.set_page_config(
    page_title="Carte Loyer Montréal",
    page_icon="🏠",
    layout="wide",
)

st.markdown("""
<style>
    .main { background: #0f1117; }
    .metric-card {
        background: #1a1d27;
        border-radius: 12px;
        padding: 16px;
        border: 1px solid #2d3048;
        margin-bottom: 12px;
    }
    .metric-card .label {
        color: #8b8fa8;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-card .value {
        color: #e8eaf6;
        font-size: 1.6rem;
        font-weight: 700;
        margin-top: 4px;
    }
    .vert  { color: #4caf50 !important; }
    .orange{ color: #ff9800 !important; }
    .rouge { color: #f44336 !important; }
    h1, h2, h3 { color: #e8eaf6 !important; }
</style>
""", unsafe_allow_html=True)

COULEUR_MAP = {
    "vert": "#39d353",
    "orange": "#ff9800",
    "rouge": "#f44336",
}


def load_json(path: Path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_loyers() -> dict:
    data = load_json(LOYERS_PATH)
    return data if isinstance(data, dict) else {}


def load_geojson() -> dict:
    data = load_json(GEOJSON_PATH)
    if not isinstance(data, dict):
        return {"type": "FeatureCollection", "features": []}
    return data


def load_cache() -> dict:
    data = load_json(CACHE_PATH)
    return data if isinstance(data, dict) else {}


def normalize_name(name: str) -> str:
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return (
        name.lower()
        .replace("-", " ")
        .replace("'", " ")
        .replace("’", " ")
        .replace("/", " ")
        .strip()
    )


def get_feature_name(feature: dict) -> str:
    props = feature.get("properties", {})
    return (
        props.get("ARRONDISSEMENT")
        or props.get("NOM")
        or props.get("name")
        or props.get("nom")
        or props.get("quartier")
        or props.get("district")
        or "Inconnu"
    )


def find_score_data(quartier: str, scores: dict) -> dict | None:
    if quartier in scores:
        return scores[quartier]

    norm_q = normalize_name(quartier)
    for k, v in scores.items():
        if normalize_name(k) == norm_q:
            return v
    return None


def filter_loyers_by_type(loyers: dict, selected_types: list[str], min_annonces: int = 3) -> dict:
    if not loyers:
        return {}

    if not selected_types:
        return loyers

    filtered = {}
    for quartier, data in loyers.items():
        types_counts = data.get("types", {})
        nb_match = sum(types_counts.get(t, 0) for t in selected_types)
        if nb_match >= min_annonces:
            filtered[quartier] = data
    return filtered


def filter_cache_points(cache: dict, selected_types: list[str]) -> list[dict]:
    points = []

    for listing in cache.values():
        lat = listing.get("lat")
        lon = listing.get("lon")
        q = listing.get("quartier_polygonal")
        t = listing.get("type_logement")

        if lat is None or lon is None or not q:
            continue

        if selected_types and t not in selected_types:
            continue

        points.append(listing)

    return points


def style_from_score(feature: dict, scores: dict):
    quartier = get_feature_name(feature)
    data = find_score_data(quartier, scores)

    if not data:
        return {
            "fillColor": "#232838",
            "color": "#2a3144",
            "weight": 1,
            "fillOpacity": 0.10,
        }

    couleur = COULEUR_MAP.get(data.get("couleur", "rouge"), "#f44336")

    return {
        "fillColor": couleur,
        "color": couleur,
        "weight": 1,
        "fillOpacity": 0.60,
    }


def build_popup_html(quartier: str, data: dict | None) -> str:
    if not data:
        return f"""
        <div style="font-family:sans-serif;min-width:220px">
            <h4 style="margin:0 0 8px;color:#333">{quartier}</h4>
            <div style="color:#666">Aucune donnée agrégée disponible.</div>
        </div>
        """

    couleur = COULEUR_MAP.get(data.get("couleur", "rouge"), "#f44336")
    trajet = data.get("temps_trajet_min")
    ratio = data.get("ratio_loyer", "?")
    score = data.get("score", "?")
    annonces = data.get("nb_annonces", "?")
    loyer = data.get("loyer_median", "?")
    metro_bonus = data.get("metro_bonus", 0)
    types = data.get("types", {})

    types_text = ", ".join([f"{k}: {v}" for k, v in sorted(types.items())]) if types else "N/A"

    return f"""
    <div style="font-family:sans-serif;min-width:280px">
        <h4 style="margin:0 0 8px;color:#333">{quartier}</h4>
        <table style="width:100%;border-collapse:collapse">
            <tr><td style="color:#666">Loyer médian</td><td><strong>{loyer} $/mois</strong></td></tr>
            <tr><td style="color:#666">Trajet</td><td><strong>{trajet} min</strong></td></tr>
            <tr><td style="color:#666">% du revenu</td><td><strong>{ratio}%</strong></td></tr>
            <tr><td style="color:#666">Bonus métro</td><td><strong>+{metro_bonus}</strong></td></tr>
            <tr><td style="color:#666">Score</td><td><strong style="color:{couleur}">{score}/100</strong></td></tr>
            <tr><td style="color:#666">Annonces</td><td>{annonces}</td></tr>
            <tr><td style="color:#666">Types repérés</td><td>{types_text}</td></tr>
        </table>
    </div>
    """


def build_heat_points(points: list[dict], scores: dict) -> list[list[float]]:
    """
    HeatMap attend [lat, lon, weight]
    """
    heat_points = []

    for listing in points:
        lat = listing.get("lat")
        lon = listing.get("lon")
        quartier = listing.get("quartier_polygonal")
        score_data = find_score_data(quartier, scores)

        if lat is None or lon is None:
            continue

        if score_data:
            weight = max(0.05, min(1.0, score_data.get("score", 0) / 100))
        else:
            weight = 0.20

        heat_points.append([lat, lon, weight])

    return heat_points


def make_map(
    scores: dict,
    geojson_data: dict,
    cache_points: list[dict],
    map_mode: str,
    workplace_address: str | None = None,
) -> folium.Map:
    m = folium.Map(
        location=[45.5017, -73.5673],
        zoom_start=10,
        tiles="CartoDB dark_matter",
    )

    if workplace_address:
        geocode_cache = load_json_cache(GEOCODE_CACHE_PATH)
        coords = geocode_address(workplace_address, geocode_cache)
        if coords:
            folium.Marker(
                location=coords,
                popup="Lieu de travail",
                tooltip="Lieu de travail",
                icon=folium.Icon(color="blue", icon="briefcase", prefix="fa"),
            ).add_to(m)

    if map_mode == "Fondu":
        heat_points = build_heat_points(cache_points, scores)
        if heat_points:
            HeatMap(
                heat_points,
                min_opacity=0.25,
                radius=32,
                blur=28,
                max_zoom=12,
            ).add_to(m)
        return m

    for feature in geojson_data.get("features", []):
        quartier = get_feature_name(feature)
        data = find_score_data(quartier, scores)

        gj = folium.GeoJson(
            data=feature,
            style_function=lambda feat, scores=scores: style_from_score(feat, scores),
            highlight_function=lambda feat: {
                "weight": 2,
                "fillOpacity": 0.75,
            },
            tooltip=folium.Tooltip(quartier),
            popup=folium.Popup(build_popup_html(quartier, data), max_width=320),
        )
        gj.add_to(m)

    return m


st.markdown("# 🏠 Carte Loyer × Pouvoir d'achat — RMR Montréal")
st.markdown("*Entrez votre salaire et votre lieu de travail pour voir les zones les plus soutenables.*")
st.divider()

loyers_raw = load_loyers()
geojson_data = load_geojson()
cache_raw = load_cache()

if not loyers_raw:
    st.error("Fichier loyers_par_quartier.json introuvable ou vide.")
    st.stop()

if not geojson_data.get("features"):
    st.error("Fichier quartiers_rmr.geojson introuvable ou vide.")
    st.stop()

if "scores" not in st.session_state:
    st.session_state.scores = None
if "workplace" not in st.session_state:
    st.session_state.workplace = None
if "salaire" not in st.session_state:
    st.session_state.salaire = None
if "filtered_points" not in st.session_state:
    st.session_state.filtered_points = []
if "map_mode" not in st.session_state:
    st.session_state.map_mode = "Quartiers"

with st.sidebar:
    st.markdown("## ⚙️ Vos paramètres")

    salaire = st.slider(
        "💰 Salaire annuel brut",
        min_value=10000,
        max_value=200000,
        value=65000,
        step=1000,
        format="$%d",
    )

    workplace = st.text_input(
        "📍 Lieu de travail",
        value="1000 rue De La Gauchetière, Montréal",
        placeholder="Adresse complète",
    )

    max_trajet = st.slider(
        "🚌 Temps de trajet max (min)",
        min_value=15,
        max_value=90,
        value=45,
        step=5,
    )

    ratio_max = st.slider(
        "🏠 % revenu max pour le loyer",
        min_value=25,
        max_value=50,
        value=33,
        step=1,
    )

    poids_transport = st.slider(
        "🚇 Importance transport (%)",
        min_value=0,
        max_value=100,
        value=40,
        step=5,
    )

    st.caption(f"{100 - poids_transport}% budget / {poids_transport}% transport")

    types_logement = st.multiselect(
        "🏠 Type de logement",
        ["1 1/2", "2 1/2", "3 1/2", "4 1/2", "5 1/2", "6 1/2"],
        default=["3 1/2", "4 1/2", "5 1/2"],
    )

    min_annonces = st.slider(
        "📊 Minimum d'annonces par quartier",
        min_value=1,
        max_value=10,
        value=2,
        step=1,
    )

    map_mode = st.radio(
        "🗺️ Mode carte",
        ["Quartiers", "Fondu"],
        index=0,
    )

    calculer = st.button("Calculer", use_container_width=True)

col_map, col_stats = st.columns([3, 1])

with col_map:
    current_scores = st.session_state.scores if st.session_state.scores else {}
    current_points = st.session_state.filtered_points if st.session_state.filtered_points else []

    m = make_map(
        current_scores,
        geojson_data=geojson_data,
        cache_points=current_points,
        map_mode=st.session_state.map_mode,
        workplace_address=st.session_state.workplace if st.session_state.scores else None,
    )

    st_folium(m, height=620, use_container_width=True)

with col_stats:
    if st.session_state.scores:
        scores = st.session_state.scores
        sal = st.session_state.salaire
        revenu_net = round(sal / 12 * 0.75)

        verts = [q for q, d in scores.items() if d.get("couleur") == "vert"]
        oranges = [q for q, d in scores.items() if d.get("couleur") == "orange"]
        rouges = [q for q, d in scores.items() if d.get("couleur") == "rouge"]

        st.markdown(f"""
        <div class="metric-card">
            <div class="label">Revenu mensuel net estimé</div>
            <div class="value">{revenu_net:,} $</div>
        </div>
        <div class="metric-card">
            <div class="label">Zones accessibles</div>
            <div class="value vert">{len(verts)} quartiers</div>
        </div>
        <div class="metric-card">
            <div class="label">Zones limites</div>
            <div class="value orange">{len(oranges)} quartiers</div>
        </div>
        <div class="metric-card">
            <div class="label">Zones difficiles</div>
            <div class="value rouge">{len(rouges)} quartiers</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("### Top 5 quartiers")
        sorted_scores = sorted(
            [(q, d) for q, d in scores.items() if "score" in d],
            key=lambda x: x[1]["score"],
            reverse=True,
        )

        for q, d in sorted_scores[:5]:
            emoji = "🟢" if d["couleur"] == "vert" else "🟠" if d["couleur"] == "orange" else "🔴"
            trajet = d.get("temps_trajet_min", "?")
            st.markdown(f"""
            <div class="metric-card" style="padding:10px 14px">
                <div style="font-weight:600;color:#e8eaf6">{emoji} {q}</div>
                <div style="color:#8b8fa8;font-size:0.85rem;margin-top:4px">
                    {d['loyer_median']:.0f}$/m · {trajet} min · bonus métro +{d.get('metro_bonus', 0)} · score {d['score']}
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("Renseigne tes paramètres puis clique sur Calculer.")

if calculer and workplace:
    loyers = filter_loyers_by_type(
        loyers_raw,
        selected_types=types_logement,
        min_annonces=min_annonces,
    )
    filtered_points = filter_cache_points(cache_raw, selected_types=types_logement)

    if not loyers:
        st.warning("Aucun quartier avec assez d'annonces pour ce type de logement.")
    else:
        with st.spinner("Calcul en cours..."):
            try:
                scores = compute_all_scores(
                    quartiers=loyers,
                    workplace_address=workplace,
                    salaire_annuel=salaire,
                    max_trajet_min=max_trajet,
                    ratio_max=ratio_max / 100,
                    poids_transport=poids_transport / 100,
                )
                st.session_state.scores = scores
                st.session_state.workplace = workplace
                st.session_state.salaire = salaire
                st.session_state.filtered_points = filtered_points
                st.session_state.map_mode = map_mode
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")

if st.session_state.scores:
    st.divider()
    st.markdown("### 📋 Tableau complet")

    rows = []
    for q, d in st.session_state.scores.items():
        if "score" not in d:
            continue
        rows.append({
            "Quartier": q,
            "Loyer médian ($/mois)": int(d["loyer_median"]),
            "% du revenu": f"{d['ratio_loyer']}%",
            "Trajet (min)": d.get("temps_trajet_min", "?"),
            "Bonus métro": d.get("metro_bonus", 0),
            "Score": d["score"],
            "Verdict": d["couleur"].capitalize(),
            "Annonces": d.get("nb_annonces", "?"),
            "Types": ", ".join(f"{k}:{v}" for k, v in sorted(d.get("types", {}).items())),
        })

    df = pd.DataFrame(rows).sort_values("Score", ascending=False)

    def color_verdict(val):
        colors = {
            "Vert": "background-color:#1b3a1c;color:#4caf50",
            "Orange": "background-color:#3a2d0a;color:#ff9800",
            "Rouge": "background-color:#3a0f0f;color:#f44336",
        }
        return colors.get(val, "")

    styled = df.style.map(color_verdict, subset=["Verdict"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    csv = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        "⬇️ Télécharger CSV",
        data=csv,
        file_name="loyer_rmr_montreal.csv",
        mime="text/csv",
    )