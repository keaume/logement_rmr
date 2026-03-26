"""
Dashboard — Carte Loyer vs Pouvoir d'achat RMR Montréal
Améliorations :
- Score arrondi proprement
- Liens cliquables vers annonces Kijiji
- Popup enrichi avec liste des annonces
- Trajet fallback corrigé
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
CACHE_PATH   = SCRAPER_DIR / "data" / "kijiji_cache.json"

st.set_page_config(
    page_title="Carte Loyer Montréal",
    page_icon="🏠",
    layout="wide",
)

st.markdown("""
<style>
    .main { background: #0f1117; }
    .block-container { padding-top: 0.5rem !important; padding-left: 1rem !important; padding-right: 1rem !important; }
    header[data-testid="stHeader"] { display: none !important; }
    #MainMenu { display: none !important; }
    footer { display: none !important; }
    .stDeployButton { display: none !important; }
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
    .vert   { color: #4caf50 !important; }
    .orange { color: #ff9800 !important; }
    .rouge  { color: #f44336 !important; }
    h1, h2, h3 { color: #e8eaf6 !important; }
</style>
""", unsafe_allow_html=True)

COULEUR_MAP = {
    "vert":   "#39d353",
    "orange": "#ff9800",
    "rouge":  "#f44336",
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
        .replace("'", " ")
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


def find_score_data(quartier: str, scores: dict):
    if quartier in scores:
        return scores[quartier]
    norm_q = normalize_name(quartier)
    for k, v in scores.items():
        if normalize_name(k) == norm_q:
            return v
    return None


def get_annonces_for_quartier(quartier: str, cache: dict, max_items: int = 5) -> list:
    """Retourne les annonces Kijiji pour un quartier donné."""
    norm_q = normalize_name(quartier)
    annonces = []
    for listing in cache.values():
        q = listing.get("quartier_polygonal", "")
        if normalize_name(q) == norm_q:
            annonces.append(listing)
    # Trie par prix croissant
    annonces.sort(key=lambda x: x.get("prix", 9999))
    return annonces[:max_items]


def filter_loyers_by_type(loyers: dict, selected_types: list, min_annonces: int = 2) -> dict:
    if not loyers or not selected_types:
        return loyers
    filtered = {}
    for quartier, data in loyers.items():
        types_counts = data.get("types", {})
        nb_match = sum(types_counts.get(t, 0) for t in selected_types)
        if nb_match >= min_annonces:
            filtered[quartier] = data
    return filtered


def filter_cache_points(cache: dict, selected_types: list) -> list:
    return [
        l for l in cache.values()
        if l.get("lat") is not None
        and l.get("lon") is not None
        and l.get("quartier_polygonal")
        and (not selected_types or l.get("type_logement") in selected_types)
    ]


def style_from_score(feature: dict, scores: dict):
    quartier = get_feature_name(feature)
    data = find_score_data(quartier, scores)
    if not data:
        return {"fillColor": "#232838", "color": "#2a3144", "weight": 1, "fillOpacity": 0.10}
    couleur = COULEUR_MAP.get(data.get("couleur", "rouge"), "#f44336")
    return {"fillColor": couleur, "color": couleur, "weight": 1, "fillOpacity": 0.60}


def build_popup_html(quartier: str, data: dict | None, cache: dict) -> str:
    """Popup enrichi avec données + liste des annonces cliquables."""
    annonces = get_annonces_for_quartier(quartier, cache)

    if not data:
        annonces_html = ""
        if annonces:
            items = "".join(
                f'<li><a href="{a.get("url","#")}" target="_blank" style="color:#4a9eff">'
                f'{a.get("prix",0):.0f}$ — {a.get("type_logement","?")} — {a.get("titre","")[:40]}</a></li>'
                for a in annonces
            )
            annonces_html = f"<ul style='padding-left:16px;margin:8px 0'>{items}</ul>"

        return f"""
        <div style="font-family:sans-serif;min-width:280px;max-width:350px">
            <h4 style="margin:0 0 8px;color:#333">{quartier}</h4>
            <div style="color:#666;margin-bottom:8px">Aucune donnée de score (hors paramètres).</div>
            {annonces_html}
        </div>
        """

    couleur   = COULEUR_MAP.get(data.get("couleur", "rouge"), "#f44336")
    trajet    = data.get("temps_trajet_min", "?")
    ratio     = data.get("ratio_loyer", "?")
    score     = round(data.get("score", 0), 1)
    annonces_n = data.get("nb_annonces", "?")
    loyer     = data.get("loyer_median", "?")
    p25       = data.get("loyer_p25", "")
    p75       = data.get("loyer_p75", "")
    metro_b   = data.get("metro_bonus", 0)
    types     = data.get("types", {})
    par_type  = data.get("loyer_par_type", {})

    types_text = ", ".join(f"{k}: {v}" for k, v in sorted(types.items())) if types else "N/A"

    # Loyer par type
    par_type_rows = ""
    for t in sorted(par_type.keys()):
        par_type_rows += f"<tr><td style='color:#888;padding:2px 8px 2px 0'>{t}</td><td><strong>{par_type[t]} $/mois</strong></td></tr>"

    # Annonces cliquables
    annonces_html = ""
    if annonces:
        items = "".join(
            f'<li style="margin-bottom:4px">'
            f'<a href="{a.get("url","#")}" target="_blank" style="color:#4a9eff;text-decoration:none">'
            f'<strong>{a.get("prix",0):.0f}$</strong> · {a.get("type_logement","?")} · '
            f'{a.get("titre","")[:35]}{"..." if len(a.get("titre","")) > 35 else ""}'
            f'</a></li>'
            for a in annonces
        )
        annonces_html = f"""
        <div style="margin-top:10px;border-top:1px solid #ddd;padding-top:8px">
            <div style="color:#555;font-size:0.85em;margin-bottom:4px">
                📋 Annonces récentes ({len(annonces)} affichées)
            </div>
            <ul style="padding-left:16px;margin:0;font-size:0.85em">{items}</ul>
        </div>
        """

    par_type_section = ""
    if par_type_rows:
        par_type_section = f"""
        <tr><td colspan="2" style="padding-top:6px;color:#555;font-size:0.85em">
            <em>Loyer médian par type :</em>
        </td></tr>
        {par_type_rows}
        """

    return f"""
    <div style="font-family:sans-serif;min-width:300px;max-width:380px">
        <h4 style="margin:0 0 8px;color:#333">{quartier}</h4>
        <table style="width:100%;border-collapse:collapse;font-size:0.9em">
            <tr>
                <td style="color:#666;padding:3px 8px 3px 0">Loyer médian</td>
                <td><strong>{loyer} $/mois</strong>
                {"<span style='color:#888;font-size:0.85em'> (P25: " + str(p25) + "$ — P75: " + str(p75) + "$)</span>" if p25 else ""}
                </td>
            </tr>
            <tr>
                <td style="color:#666;padding:3px 8px 3px 0">Trajet estimé</td>
                <td><strong>{trajet} min</strong></td>
            </tr>
            <tr>
                <td style="color:#666;padding:3px 8px 3px 0">% du revenu</td>
                <td><strong>{ratio}%</strong></td>
            </tr>
            <tr>
                <td style="color:#666;padding:3px 8px 3px 0">Bonus métro</td>
                <td><strong>+{metro_b}</strong></td>
            </tr>
            <tr>
                <td style="color:#666;padding:3px 8px 3px 0">Score</td>
                <td><strong style="color:{couleur}">{score} / 100</strong></td>
            </tr>
            <tr>
                <td style="color:#666;padding:3px 8px 3px 0">Annonces</td>
                <td>{annonces_n} annonces Kijiji</td>
            </tr>
            {par_type_section}
        </table>
        {annonces_html}
    </div>
    """


def build_heat_points(points: list, scores: dict) -> list:
    heat_points = []
    for listing in points:
        lat = listing.get("lat")
        lon = listing.get("lon")
        if lat is None or lon is None:
            continue
        quartier   = listing.get("quartier_polygonal")
        score_data = find_score_data(quartier, scores) if quartier else None
        weight = max(0.05, min(1.0, score_data.get("score", 0) / 100)) if score_data else 0.2
        heat_points.append([lat, lon, weight])
    return heat_points


def make_map(scores, geojson_data, cache_points, cache, map_mode, workplace_address=None):
    m = folium.Map(
        location=[45.5017, -73.5673],
        zoom_start=10,
        tiles="CartoDB dark_matter",
    )

    if workplace_address:
        geo_cache = load_json_cache(GEOCODE_CACHE_PATH)
        coords = geocode_address(workplace_address, geo_cache)
        if coords:
            folium.Marker(
                location=coords,
                popup="💼 Lieu de travail",
                tooltip="Lieu de travail",
                icon=folium.Icon(color="blue", icon="briefcase", prefix="fa"),
            ).add_to(m)

    if map_mode == "Fondu":
        heat_points = build_heat_points(cache_points, scores)
        if heat_points:
            HeatMap(heat_points, min_opacity=0.25, radius=32, blur=28, max_zoom=12).add_to(m)
        return m

    for feature in geojson_data.get("features", []):
        quartier = get_feature_name(feature)
        data     = find_score_data(quartier, scores)
        popup_html = build_popup_html(quartier, data, cache)

        folium.GeoJson(
            data=feature,
            style_function=lambda feat, scores=scores: style_from_score(feat, scores),
            highlight_function=lambda feat: {"weight": 2, "fillOpacity": 0.80},
            tooltip=folium.Tooltip(
                f"{quartier}" + (f" — {find_score_data(quartier, scores)['loyer_median']}$/m · score {round(find_score_data(quartier, scores)['score'], 1)}" if find_score_data(quartier, scores) else ""),
            ),
            popup=folium.Popup(popup_html, max_width=400),
        ).add_to(m)

    return m


# ── App ───────────────────────────────────────────────────────────────────────

loyers_raw   = load_loyers()
geojson_data = load_geojson()
cache_raw    = load_cache()

if not loyers_raw:
    st.error("Fichier loyers_par_quartier.json introuvable ou vide.")
    st.stop()

if not geojson_data.get("features"):
    st.error("Fichier quartiers_rmr.geojson introuvable ou vide.")
    st.stop()

for key in ["scores", "workplace", "salaire", "filtered_points", "map_mode"]:
    if key not in st.session_state:
        st.session_state[key] = None if key != "filtered_points" else []
if st.session_state.map_mode is None:
    st.session_state.map_mode = "Quartiers"

with st.sidebar:
    st.markdown("## ⚙️ Vos paramètres")

    salaire = st.slider("💰 Salaire annuel brut", 10000, 200000, 65000, 1000, format="$%d")
    workplace = st.text_input("📍 Lieu de travail", value="1000 rue De La Gauchetière, Montréal")
    max_trajet = st.slider("🚌 Trajet max (min)", 15, 90, 45, 5)
    ratio_max = st.slider("🏠 % revenu max pour le loyer", 25, 50, 33, 1)
    poids_transport = st.slider("🚇 Importance transport (%)", 0, 100, 40, 5)
    st.caption(f"{100 - poids_transport}% budget / {poids_transport}% transport")

    types_logement = st.multiselect(
        "🏠 Type de logement",
        ["1 1/2", "2 1/2", "3 1/2", "4 1/2", "5 1/2", "6 1/2"],
        default=["3 1/2", "4 1/2", "5 1/2"],
    )
    min_annonces = st.slider("📊 Minimum d'annonces par quartier", 1, 10, 2, 1)
    map_mode = st.radio("🗺️ Mode carte", ["Quartiers", "Fondu"], index=0)
    calculer = st.button("🔍 Calculer", use_container_width=True)

st.markdown("## 🏠 Carte Loyer × Pouvoir d’achat — RMR Montréal")

col_map, col_stats = st.columns([4, 1])

with col_map:
    m = make_map(
        scores=st.session_state.scores or {},
        geojson_data=geojson_data,
        cache_points=st.session_state.filtered_points or [],
        cache=cache_raw,
        map_mode=st.session_state.map_mode,
        workplace_address=st.session_state.workplace if st.session_state.scores else None,
    )
    st_folium(m, height=780, use_container_width=True)

with col_stats:
    if st.session_state.scores:
        scores = st.session_state.scores
        sal    = st.session_state.salaire
        revenu_net = round(sal / 12 * 0.75)

        verts   = [q for q, d in scores.items() if d.get("couleur") == "vert"]
        oranges = [q for q, d in scores.items() if d.get("couleur") == "orange"]
        rouges  = [q for q, d in scores.items() if d.get("couleur") == "rouge"]

        st.markdown(f"""
        <div class="metric-card">
            <div class="label">Revenu mensuel net estimé</div>
            <div class="value">{revenu_net:,} $</div>
        </div>
        <div class="metric-card">
            <div class="label">Zones accessibles 🟢</div>
            <div class="value vert">{len(verts)} quartiers</div>
        </div>
        <div class="metric-card">
            <div class="label">Zones limites 🟠</div>
            <div class="value orange">{len(oranges)} quartiers</div>
        </div>
        <div class="metric-card">
            <div class="label">Zones difficiles 🔴</div>
            <div class="value rouge">{len(rouges)} quartiers</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("### 🏆 Top 5 quartiers")
        sorted_scores = sorted(
            [(q, d) for q, d in scores.items() if "score" in d],
            key=lambda x: x[1]["score"],
            reverse=True,
        )

        for q, d in sorted_scores[:5]:
            emoji  = "🟢" if d["couleur"] == "vert" else "🟠" if d["couleur"] == "orange" else "🔴"
            trajet = d.get("temps_trajet_min", "?")
            score  = round(d["score"], 1)
            st.markdown(f"""
            <div class="metric-card" style="padding:10px 14px">
                <div style="font-weight:600;color:#e8eaf6">{emoji} {q}</div>
                <div style="color:#8b8fa8;font-size:0.85rem;margin-top:4px">
                    {d['loyer_median']:.0f}$/m · {trajet} min · score <strong style="color:#e8eaf6">{score}</strong>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("👈 Renseigne tes paramètres puis clique **Calculer**.")
# ── Calcul ────────────────────────────────────────────────────────────────────

if calculer and workplace:
    loyers = filter_loyers_by_type(loyers_raw, selected_types=types_logement, min_annonces=min_annonces)
    filtered_points = filter_cache_points(cache_raw, selected_types=types_logement)

    if not loyers:
        st.warning("Aucun quartier avec assez d'annonces pour ce type de logement. Diminue le minimum d'annonces.")
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
                st.session_state.scores         = scores
                st.session_state.workplace      = workplace
                st.session_state.salaire        = salaire
                st.session_state.filtered_points = filtered_points
                st.session_state.map_mode       = map_mode
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")

# ── Tableau ───────────────────────────────────────────────────────────────────

if st.session_state.scores:
    st.divider()
    st.markdown("### 📋 Tableau complet")

    rows = []
    for q, d in st.session_state.scores.items():
        if "score" not in d:
            continue
        rows.append({
            "Quartier":            q,
            "Loyer médian ($/mois)": int(d["loyer_median"]),
            "P25 ($)":             d.get("loyer_p25", ""),
            "P75 ($)":             d.get("loyer_p75", ""),
            "% du revenu":         f"{d['ratio_loyer']}%",
            "Trajet (min)":        d.get("temps_trajet_min", "?"),
            "Score":               f'{round(float(d["score"]), 1):.1f}',
            "Verdict":             d["couleur"].capitalize(),
            "Annonces":            d.get("nb_annonces", "?"),
            "Types":               ", ".join(f"{k}:{v}" for k, v in sorted(d.get("types", {}).items())),
        })

    df = pd.DataFrame(rows).sort_values("Score", ascending=False)

    def color_verdict(val):
        return {
            "Vert":   "background-color:#1b3a1c;color:#4caf50",
            "Orange": "background-color:#3a2d0a;color:#ff9800",
            "Rouge":  "background-color:#3a0f0f;color:#f44336",
        }.get(val, "")

    st.dataframe(
        df.style.map(color_verdict, subset=["Verdict"]),
        use_container_width=True,
        hide_index=True,
    )

    csv = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ Télécharger CSV", data=csv, file_name="loyer_rmr_montreal.csv", mime="text/csv")