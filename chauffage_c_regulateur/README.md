# Régulation intelligente – Circuit C

**Application indépendante de Diematic to MQTT** : aucun code Modbus, aucune modification de la passerelle existante.

## V0.3.5 — Diagnostics de confort par pièce (comparaison, sans déploiement automatique)

Cette version cherche à préserver les économies **sans surchauffer toute la maison
à cause d'une pièce localement déficitaire** (notamment le Salon TV).
Elle n'infère pas la cause d'un déficit : vérifier séparément circulation,
radiateur, tête thermostatique et pont thermique.

- `comfort_guard_enabled: false` **par défaut**. L'algorithme V0.3.4
  continue d'envoyer **exactement ses propres décisions** à la Diematic.
  La nouvelle consigne est uniquement calculée à titre comparatif.
- **Déficit local** : une seule pièce présentant au moins 0,5 °C
  de déficit pendant 60 minutes et au moins 8 zones valides. La proposition
  V0.3.5 plafonne l'influence supplémentaire de cette pièce dans le calcul,
  sans supprimer sa demande de chauffage.
- **Déficit collectif** : deux pièces ou davantage présentant au moins
  0,5 °C de déficit pendant 45 minutes. Sur les 15 minutes suivantes, la
  nouvelle proposition supprime progressivement la correction météo négative.
  Toute correction météo positive reste disponible.
- **Hystérésis météo de 15 minutes** : une nouvelle réduction météo doit
  persister avant d'être appliquée ; un réchauffement de la consigne est
  immédiat dans la proposition V0.3.5.
- Les capteurs MQTT `sensor.chauffage_c_mode_confort`,
  `sensor.chauffage_c_deficit_pieces_24h` et
  `sensor.chauffage_c_ecart_simulation_v035` enregistrent les diagnostics
  à intervalles de cinq minutes. Les attributs du déficit contiennent un
  classement par pièce, les deux propositions de consigne et leurs corrections
  météo. Ces valeurs sont indicatives, pas des preuves d'une panne physique.
- Le suivi des déficits est conservé dans `/data/thermal_state.json`.
  L'algorithme, la loi d'eau et les plannings des têtes thermostatiques
  existants ne sont pas modifiés par le mode comparatif.

Pour tester : mettre à jour l'add-on sur une installation de test, conserver
`comfort_guard_enabled: false` et comparer les capteurs au moins 48 à 72 h.
**Ne pas activer le mode de pilotage V0.3.5 sans avoir validé les journaux,
l'équilibrage thermique et la sécurité de la chaudière.** Aucun automatisme
ne réalise cette activation à votre place.

## Principe
Cette application consulte les thermostats Danfoss Link et Eve via l'API Home Assistant, estime la demande globale et ajuste **uniquement** les deux consignes d'ambiance de la zone C : `number.zone_c_temp_day` et `number.zone_c_temp_night`. La loi d'eau, la vanne mélangeuse et la chaudière restent contrôlées par Diematic.

## Sécurité et mise en service
- Installation **désactivée** : `automatic_control: false` et simulation `dry_run: true` par défaut.
- Ne pas activer avant vérification de la communication HA, des thermomètres, de l'influence de la consigne d'ambiance et des traces du régulateur.
- En cas d'anomalie critique, repli sur 21 °C jour / 19 °C nuit, uniquement si le régulateur a effectivement piloté la chaudière.
- Lors d'une désactivation ou d'un arrêt normal après un pilotage actif, restauration des consignes de référence.
- Les 2 Eve sont agrégées en une seule zone. L'ancien thermostat Danfoss de cette chambre est exclu.
- Aucune ouverture/fermeture de vanne, aucun accès direct Modbus.
- V1 **heuristique**, pas encore un apprentissage statistique. Les observations permettront la calibration.
- Les radiateurs sans tête thermostatique peuvent toujours chauffer lors des demandes des chambres : contrainte hydraulique non contournable.

## Architecture
- `app.py` exécute un cycle de mesure toutes les 30 secondes.
- En simulation, écrit les décisions dans le journal, mais **ne modifie aucune entité**.
- En pilotage autorisé, écriture au maximum toutes les 120 secondes, sauf repli de sécurité.
- API HA via `SUPERVISOR_TOKEN` (Home Assistant OS).
- Pour les analyses, les logs indiquent : zones valides, demandes, écart maximal, pièce prioritaire, consigne proposée et action réalisée.

## Installation
Quand la branche est fusionnée dans `main`, actualiser le dépôt d'applications HA déjà connecté, puis installer « Régulation intelligente – Circuit C ». **Ne pas désinstaller ni mettre à jour Diematic pour cela.** Conserver `automatic_control: false` et `dry_run: true` pendant les premiers contrôles.

Pour autoriser réellement les commandes, les deux options doivent être : `automatic_control: true` et `dry_run: false`. Une fois vérifié, le tableau de bord HA peut afficher les états existants de chaque pièce et de la Diematic ; les métriques spécifiques seront ajoutées dans la V2.

## Pièces surveillées
Danfoss : séjour, cuisine, entrée, bureau, buanderie, Adaline, Octave, invités, Monique, SDB Valéric. Les mesures indisponibles sont exclues du calcul. Eve : thermomètre central, deux positions de vannes, consigne active groupée.

## Limites V1
Il ne s'agit pas encore d'un modèle prédictif : le calcul réagit à la demande actuelle, et la météo extérieure influence déjà la loi d'eau Diematic. La prévision et l'apprentissage seront ajoutés après collecte et vérification des historiques. Contrôler particulièrement les pièces sans tête thermostatique et la dynamique de la vanne mélangeuse.

## Anticipation météo optionnelle V0.1.2

- Option `weather_anticipation: false` par défaut : comportement inchangé avant activation.
- Prévisions horaires `weather.forecast_maison` via `weather.get_forecasts`, interrogées toutes les 15 minutes.
- Si au moins une pièce demande du chauffage, bonus virtuel de +0,5 °C pour une baisse extérieure prévue de 2 °C et +1 °C pour 4 °C ou davantage, dans les 1 à 3,5 heures.
- Plafond configurable avec `weather_max_boost` (0 à 1 °C). Les bornes et limites de variation V1 restent actives.
- Pas de bonus si les prévisions sont indisponibles, moins de 8 pièces valides ou aucune demande.
- Vérifier d'abord en simulation (`dry_run: true`). La prise en compte du soleil et de l'inertie apprise viendra plus tard.

## V0.1.3 — Pré-chauffage prédictif
- Le refroidissement extérieur annoncé peut désormais déclencher un bonus même sans demande de chauffe, lorsqu'au moins une pièce se trouve à moins de 0,35 °C au-dessus de sa consigne.
- Aucune anticipation si toutes les pièces sont confortablement au-dessus de leur consigne ; pas de préchauffage sans prévisions.
- Les journaux affichent les consignes sans météo et avec météo (après limitation de vitesse), et le nombre de zones proches de leur consigne.
- L'option météo reste désactivée par défaut pour une installation sans surprise ; activer explicitement `weather_anticipation`.

## V0.1.4 — anticipation météo bidirectionnelle
- Prévisions horaires pondérées sur 1 à 6 heures, avec priorité aux deux prochaines heures.
- Refroidissement : correction positive si pièce en demande ou proche de sa consigne.
- Réchauffement : correction négative, sauf si une pièce manque de plus de 0,5 °C.
- Journaux : variation prévue, correction signée, consigne sans et avec météo.
