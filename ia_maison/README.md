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
