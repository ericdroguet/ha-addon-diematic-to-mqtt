# Prochaine étape — pilotage des têtes thermostatiques (préparation, non activé)

**État actuel :** régulateur Circuit C v0.3.3 pilote uniquement `number.zone_c_temp_day` et `number.zone_c_temp_night`. Les têtes Danfoss / Eve conservent leurs propres consignes et plannings.

## Objectif
Un **planificateur central par pièce** calcule les consignes et l'ouverture thermique nécessaires, tandis que Circuit C n'apporte que la température d'eau réellement requise par les pièces en demande. Confort prioritaire, minimisation de la consommation à conditions comparables.

## Étapes ordonnées
1. **Observer en lecture seule** : pour chacune des 11 zones Danfoss et la chambre Eve, relever `current_temperature`, `temperature`, `hvac_action`, `room_state`, programme hebdomadaire, `at_home_setpoint`, `away_setpoint`, états de dérogation. Suivre les vitesses de chauffage/refroidissement par zone, corrélées à `sensor.ext_temp`, température de départ et heure.
2. **Tester les commandes et la persistance des horaires sans automatiser** : vérifier que `climate.set_temperature` et `climate.set_hvac_mode` sont effectivement acceptées par l'intégration `danfoss_link`, si une consigne auto est une dérogation temporaire ou écrase le planning, et comment revenir explicitement au programme. Ne pas supposer que le comportement d'Eve est identique.
3. **Mode simulation** : générer par zone une consigne recommandée, heure optimale de préchauffage et durée anticipée, comparer aux mesures réelles et aux changements manuels Apple Maison, sans toucher aux têtes.
4. **Pilote sur Salon TV seulement** : premières commandes bornées (demande de confort / préchauffage) avec limites de fréquence, retour automatique au planning et bouton de désactivation immédiate. Respecter prioritairement une modification de consigne faite dans Apple Maison ou sur Danfoss.
5. **Déployer progressivement** : séjour / cuisine, autres pièces, puis chambre Eve (deux radiateurs coordonnés par le thermostat agrégé, ne pas écrire directement les deux têtes à l'aveugle). Gérer correctement les modes maison *schedule, at_home, vacation, pause* et les différentes périodes chambres/jour/nuit.
6. **Optimisation optionnelle par IA** : modèle d'inertie/apprentissage de chaque pièce et correction météo pour calculer *quand* commencer le préchauffage, *quelle consigne* et *quel besoin global* ; contrôle matériel local déterministe et plafonné des sorties. Ne jamais dépendre d'un appel cloud à l'IA pour assurer une température minimale ou restaurer un mode sûr.

## Contraintes non négociables
- Ne jamais lutter contre les horaires natifs ou dérogations manuelles sans savoir comment elles sont représentées.
- Fallback autonome si Home Assistant, Danfoss Link, météo ou modèle IA deviennent indisponibles.
- Modes absence et hors-gel prioritaires ; bornes par pièce ; pas de commandes répétées à chaque mesure de 30 s.
- Journaliser **qui** a fixé une consigne (horaire / utilisateur / algorithme), pourquoi et pour combien de temps.
- Évaluer systématiquement confort (écarts cumulés) et fioul (DDJ corrigés) face à la version précédente.

**Important :** ce document ne commande aucun équipement, aucun pilotage individuel n'est encore activé.
