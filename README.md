# 🏠 Carte Loyer × Pouvoir d'achat — RMR Montréal

Outil interactif pour trouver le meilleur quartier selon ton salaire et ton lieu de travail.

## Stack
- **Python** — scraping + calculs
- **BeautifulSoup** — scraper Kijiji
- **Streamlit** — dashboard web
- **Folium** — carte interactive
- **Nominatim** — géocodage gratuit (OpenStreetMap)
- **Navitia.io** — temps de trajet TC réels (données GTFS STM)
- **OpenRouteService** — fallback si Navitia indisponible

## Installation

```bash
pip install -r requirements.txt
```

## Étape 1 — Scraper les loyers

```bash
cd scraper
python kijiji_scraper.py
```

Génère `data/kijiji_cache.json` et `data/loyers_par_quartier.json`.
Stratégie delta : ne re-scrape jamais ce qui est déjà en cache.

## Étape 2 — Clés API gratuites

### Navitia.io (temps de trajet TC réels)
1. Créer un compte sur https://navitia.io
2. Copier le token dans `scraper/transit_scorer.py` → `NAVITIA_TOKEN`

### OpenRouteService (fallback)
1. Créer un compte sur https://openrouteservice.org/dev/#/signup
2. Copier la clé dans `scraper/transit_scorer.py` → `ORS_API_KEY`

## Étape 3 — Lancer le dashboard

```bash
streamlit run dashboard/app.py
```

Ouvre http://localhost:8501

## Structure

```
loyer-rmr/
├── scraper/
│   ├── kijiji_scraper.py     # Collecte les annonces Kijiji
│   └── transit_scorer.py     # Calcul temps de trajet + score
├── dashboard/
│   └── app.py                # Interface Streamlit
├── data/
│   ├── kijiji_cache.json     # Cache annonces (ne pas supprimer!)
│   ├── geocode_cache.json    # Cache géocodage Nominatim
│   ├── transit_cache.json    # Cache temps de trajet
│   └── loyers_par_quartier.json  # Données agrégées
└── requirements.txt
```

## Logique du score

```
Score (0-100) = loyer_score × 60% + trajet_score × 40%

loyer_score  = basé sur ratio loyer/revenu net vs seuil configuré (défaut 33%)
trajet_score = 100 si < max_trajet, pénalité progressive au-delà

🟢 Vert   : score ≥ 65
🟠 Orange : score 40-65
🔴 Rouge  : score < 40
```

## Utilisation syndicat de logement

Les données CSV exportables permettent de :
- Documenter l'inaccessibilité au logement par tranche de revenu
- Illustrer le "rayon de vie" réel selon différents salaires
- Croiser avec les données de demande d'augmentation de loyer (TAL)
- Produire des visualisations pour négociations et mémoires

---
Données Kijiji scrappées à titre informatif. Compléter avec données SCHL pour validation.
