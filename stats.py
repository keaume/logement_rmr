import json

cache = json.load(open('scraper/data/kijiji_cache.json', encoding='utf-8'))
loyers = json.load(open('scraper/data/loyers_par_quartier.json', encoding='utf-8'))

print(f'Annonces en cache : {len(cache)}')
print(f'Quartiers agreges : {len(loyers)}')
print()
print('Top 10 :')
for i, (q, s) in enumerate(list(loyers.items())[:10], 1):
    print(f'  {i:2}. {q:<35} {s["nb_annonces"]:>3} annonces  ~{s["loyer_median"]}$/mois')