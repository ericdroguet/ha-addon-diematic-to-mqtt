# IA Maison - Meylan / 0.1.0

Observation locale exclusivement. GET /api/states toutes les 60 s via le cache HA.
Aucune commande, aucun accès Modbus, aucun appel Netatmo, aucune API OpenAI.
Pas de lecture de SQLite Recorder. Archives gzip CRC par membre, segment horaire
et identifiant de démarrage. Réserve disque 5 Gio, quota cible 5 Gio (contrôle
horaire), durée cible 365 jours sous réserve du quota. Aucun accès aux archives
des autres applications. Modèle sauvegardé atomiquement toutes les 5 minutes.
En cas de redémarrage, trou de données, changement de pente ou bande V3V, la
fenêtre d'apprentissage incomplète est abandonnée. Après panne, trois lectures
réussies avant reprise. Valeurs non numériques/états inconnus conservés comme
qualité, pas imputés à zéro. Rapports anciens des thermostats exclus du modèle;
une valeur stable ne prouve pas un capteur figé. Aucun historique Recorder importé.
Découverte à chaque collecte des entités des domaines utiles (y compris nouveaux
capteurs Wilo); pas de texte libre, caméra, coordonnées GPS ni données de secrets.
Les états de présence home/not_home restent locaux. Les attributs non sélectionnés
ne sont pas enregistrés. Le modèle utilise seulement un sous-ensemble thermique.
Prédiction expérimentale à 30 min par zone climate, régression adaptative légère
évaluée sur les observations suivantes contre une prédiction de persistance.
Les prévisions ne sont pas validées et n'ont aucune autorité; les changements de
cible des pièces, sauts aberrants, mesures manquantes et trous invalident les fenêtres.
Pas encore de modèle causal, MPC, prévisions météo intégrées ni optimisation Wilo.
Écran ingress / et /api/status en JSON (état, qualité, prévisions, erreurs).
Stopper l'application suffit pour suspendre la collecte. Ne pas désinstaller
sans sauvegarde de /data si l'on souhaite garder les données.

## 0.1.1 — Supervision visuelle
Interface HTML locale responsive via Ingress, actualisée toutes les 15 s depuis le statut local. Affiche qualité des états, archives, nombre de prédictions évaluées et erreurs par pièce, avec référence de persistance. Aucun changement du modèle ni des permissions. Compteurs de collecte et d'interruptions par session.

## 0.1.2 — Mémoire humaine
Liste des entités non privées par qualité, recherche, rôle et annotation modifiables, journal horodaté des événements manuels. Les notes sont persistées atomiquement dans `/data/observation/human_knowledge.json`, séparées des mesures et du modèle. Écriture locale via Ingress avec jeton CSRF par démarrage ; aucune commande HA. Fichier corrompu conservé et écriture bloquée. Les annotations n'altèrent ni la qualité des capteurs ni les modèles actuels ; elles préparent l'enrichissement futur. Ne pas saisir de secrets. Les 100 derniers événements sont affichés ; 10 000 maximum conservés, suppression individuelle possible. Les entités de présence privées ne figurent pas dans le catalogue.

## 0.1.3 — Préclassification
Suggestions déterministes d'après identifiant, domaine, unité et classe de mesure. Distinctes des annotations humaines et des états physiques. Filtre prioritaire pour les cas sans suggestion ; suggestions préremplies, confirmation individuelle facultative. Aucun changement du modèle, des mesures ou des équipements. Endpoint local de synthèse `/api/knowledge/summary`. Pas encore d'analyse du registre des intégrations.

## 0.1.4 — Accès ciblé à la mémoire
Endpoints Ingress locaux `/api/knowledge/session` et `/api/knowledge/entity?id=...` pour lire le jeton et vérifier une annotation sans transférer le catalogue complet. Les écritures restent protégées par le même jeton CSRF. Aucun changement du modèle ni des permissions.

## 0.1.5 — Modèle thermique parallèle expérimental
Profils structurés locaux versionnés (surface, hauteurs, émetteurs, voisins,
combles, climatisations, ouvrants). Aucune donnée privée incluse dans le dépôt.
GET/POST /api/thermal/profiles via Ingress, même protection CSRF que la mémoire.
POST remplace le profil complet après validation contre le catalogue observé.
Stockage séparé thermal_profiles.json et thermal_shadow.json dans /data/observation.
Une modification du profil réinitialise uniquement le modèle expérimental de la zone.
Surfaces et hauteur moyenne explicitement connue normalisent les termes de chauffage ;
hauteurs min/max et catégorie qualitative d'isolation restent descriptives. Les termes
hydrauliques, échanges voisins, combles, PAC et occultation sont des indicateurs,
pas des puissances ni un bilan causal. Une mesure mappée indisponible exclut la zone
pour cette fenêtre. Le modèle initial reste inchangé. Comparaison sur mêmes instants
avec l'ancien modèle et persistance, validation toujours false ; aucun pilotage.

## 0.1.6 — Soleil et météo locale
Troisième modèle indépendant, comparé au modèle thermique sans solaire sur les mêmes instants. Les profils locaux acceptent solar_surfaces (azimuth en degrés depuis le nord, tilt 0 horizontal/90 vertical, poids relatif optionnel, cover optionnel). Indice géométrique normalisé corrigé de nébulosité, non irradiance mesurée, sans estimation de watts. Sans occultation fiable l'indice est une opportunité solaire, pas un apport réel ; un cover explicitement lié mais périmé exclut la prévision de jour. Les prévisions solaires 6/24/72h déjà présentes dans HA sont affichées en contexte, pas encore injectées comme mesures instantanées. Aucun nouvel appel météo ou cloud. Le modèle 0.1.5 et son fichier restent intacts, les données privées demeurent locales. Aucun pilotage.

## 0.1.7 — Mémoire de commande RTS
Les profils locaux peuvent déclarer solar_cover_memory (liste explicite de input_boolean locaux synchronisés avec les événements des rideaux). Une valeur stable reste exploitable après une heure, sans être assimilée à une mesure physique. L'indice solaire direct est modulé par la fraction de mémoires ouvertes, à poids égaux, approximation non photométrique. Un état absent/invalide exclut le calcul. Ne mapper que les mémoires initialisées et les correspondances confirmées. Le modèle thermique de référence reste indépendant. Aucun appel cloud ni commande d'équipement.
