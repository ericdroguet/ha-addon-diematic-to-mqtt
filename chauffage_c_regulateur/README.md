# Régulation intelligente – Circuit C

**Application indépendante de Diematic to MQTT** : aucun code Modbus, aucune modification de la passerelle existante.

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


## V0.4.0 — surveillance hydraulique sans action sur le chauffage

Deux DS18B20 reliées au Shelly Plus Uni mesurent le circuit C :
- **101, départ** : \`sensor.shellyplusuni_c88a7beb1558_temperature_2\`
- **100, retour** : \`sensor.shellyplusuni_c88a7beb1558_temperature\`

L'observateur \`thermal_monitor.py\` fonctionne dans un fil séparé du régulateur, et ne fait
que lire les entités HA et publier des mesures MQTT. Il ne pilote **ni la chaudière, ni la
vanne mélangeuse, ni les consignes virtuelles**. La logique de contrôle v0.3.4 reste inchangée.

Entités découvertes automatiquement :
- \`sensor.chauffage_c_thermique_delta_t\` : départ moins retour, **signé** ;
- \`sensor.chauffage_c_thermique_delta_t_5min\`, \`_15min\`, \`_30min\` :
  médianes glissantes après couverture suffisante de leur fenêtre ;
- \`sensor.chauffage_c_thermique_ecart_depart_diematic\` : écart Shelly 101 - sonde Diematic ;
- \`sensor.chauffage_c_thermique_etat\` : état de validité.

Les températures doivent être numériques, plausibles et récentes (< 10 minutes).
Si une sonde est indisponible, trop ancienne ou si le ΔT signé est incohérent, les valeurs
thermiques passent indisponibles : on ne confond plus une panne de sonde avec 0 °C.
Les filtres redémarrent après une panne ; ils ne mélangent pas l'avant et l'après.

Les publications MQTT sont non retenues pour les mesures et expirent sous 180 secondes ;
le seul état conservé par MQTT est la disponibilité courante de l'observateur.
Le capteur historique \`sensor.circuit_c_delta_t_depart_retour\` (Min/Max) est conservé
pour compatibilité avec les anciennes cartes, mais ne doit **pas** servir au futur pilotage.

**Précaution** : les lectures du retour peuvent refléter le temps de réponse de la sonde
et du tuyau, et le ΔT ne suffit pas à mesurer la puissance sans débitmètre. Pas de
correction directe des consignes en v0.4.0 ; prochaines étapes : observation,
comparaison au confort, simulation, puis validation avant tout pilotage basé sur le ΔT.

**Retour arrière** : réinstaller la v0.3.4. Les consignes jour/nuit, les réglages, les
horaires et l'application Modbus indépendante n'ont pas changé.
