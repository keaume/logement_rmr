# 🏠 Carte Loyer × Pouvoir d'achat --- RMR Montréal

Outil interactif pour identifier les quartiers les plus accessibles
selon le salaire, le lieu de travail et l'importance accordée au
transport.

------------------------------------------------------------------------

## 🚀 Fonctionnalités

-   Scraping des annonces Kijiji
-   Agrégation des loyers par quartier
-   Carte interactive avec code couleur :
    -   🟢 favorable
    -   🟠 compromis
    -   🔴 défavorable
-   Score personnalisable :
    -   salaire
    -   temps de trajet max
    -   \% revenu max pour le loyer
    -   importance transport vs budget
-   Bonus simple de proximité métro
-   Export CSV des résultats

------------------------------------------------------------------------

## 🧠 Stack technique

-   Python
-   BeautifulSoup
-   Streamlit
-   Folium
-   Pandas
-   Nominatim (OpenStreetMap)
-   GeoJSON quartiers RMR

------------------------------------------------------------------------

## ⚙️ Installation

``` bash
pip install -r requirements.txt
```

------------------------------------------------------------------------

## 📥 Scraper les loyers

``` bash
cd scraper
python kijiji_scraper.py
```

------------------------------------------------------------------------

## 🖥️ Lancer le dashboard

``` bash
python -m streamlit run dashboard/app.py
```

Puis ouvrir : http://localhost:8501

------------------------------------------------------------------------

## 📊 Logique du score

Score = (budget × (1 - poids_transport)) + (transport × poids_transport)

------------------------------------------------------------------------

## ⚠️ Limites

-   Données Kijiji non exhaustives
-   Certaines annonces sans adresse
-   Temps de trajet estimé (pas STM réel complet)

------------------------------------------------------------------------

## 👨‍💻 Auteur

Antoine Toenz\
Email : antoine@toenz.com
