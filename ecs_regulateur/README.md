# Régulateur ECS indépendant — 0.1.0

Application Home Assistant Python, **observation stricte** : GET uniquement, aucun MQTT publish, aucune commande physique. N'altère ni Diematic MQTT ni le régulateur C. Installation et démarrage manuels.

## Stratégie validée le 27 novembre 2026

- Bleu/blanc HP et HC : fioul 50 °C, complément électrique jusqu'à 55 °C ou 120 minutes maximum (4,8 kWh théoriques à 2,4 kW).
- Rouge HP : résistance interdite, fioul 55 °C. Rouge HC : complément autorisé.
- Ne pas redémarrer le complément après échéance sans nouveau cycle.
- Arrêt en cas de données invalides, tarif interdit, fin de cible, pompe ECS active pendant complément ou retrait d'autorisation.
- Le délai est un délai mural persistant, pas remis à zéro au redémarrage.
- Réarmement proposé sous 48 °C ; attendre 50 °C et l'arrêt de la pompe ECS (post-circulation comprise). Pas de complément au premier démarrage au milieu d'un cycle. Ce sont des hypothèses de simulation à vérifier, pas des commandes.
- La consigne électrique est une cible logicielle ; la Diematic ne reçoit que 50 ou 55 dans les propositions.
- Garde-fous : mode hiver mixte, automatisme autorisé, boost manuel inactif, protection de chauffe inefficace inactive, fraîcheur de la sonde.

## Fichiers

- policy.py : machine d'états pure, facilement testable.
- app.py : acquisition HA, persistance atomique et logs.
- test_policy.py : tests du tarif, des durées, de la reprise et des sécurités.
- config.yaml : paramètres dans l'UI de l'application, aucun YAML utilisateur à éditer.
- /data/cycle.json : état simulé durable ; /data/events.jsonl : décisions et mesures observées, rotation à 2 Mo.

## Limites et prochaines étapes

Les propositions ne sont PAS appliquées. Le fioul actuel peut encore être à 55 °C ; le complément simulé ne sera donc pas forcément déclenché. Les 4,8 kWh sont un plafond théorique, pas une consommation mesurée. Les statistiques de coût réel par cycle nécessitent un compteur de résistance ou une estimation explicitement identifiée, et l'intégration du suivi fioul. Pas encore de capteurs HA dédiés, ni de tableau de bord ECS supplémentaire.

Avant tout pilotage : auditer les automatisations existantes, vérifier les seuils de reprise et la priorité ECS, le thermostat physique, la température de retour du bouclage, le volume et la sécurité sanitaire ; valider une stratégie de rattrapage en cas de timeout. Prévoir un mécanisme indépendant de coupure de la résistance en cas de perte HA ou arrêt du programme. Ne pas se contenter d'une minuterie Python comme sécurité physique. Prévoir une procédure de retour au fonctionnement fioul normal. Ne jamais modifier la sécurité constructeur. Le contrôleur actif fera l'objet d'une version distincte et d'une autorisation explicite.

Tests : python -m unittest -v test_policy (également lors de la construction de l'image).

## 0.1.1 – pilotage réel

Autorisation explicite input_boolean.ecs_hybride_actif. Minuterie restaurable timer.ecs_hybride_limite et automatisation de coupure seule automation.ecs_hybride_parachute_electrique obligatoires. Coupure 119 min maximum, retour fioul 55 °C sur échec ou désactivation. Pas de reprise électrique après arrêt externe ou redémarrage. Anciennes commandes automatiques et boost manuel 3h désactivés. La sécurité HA n'est pas indépendante d'une panne HA ou Netatmo ; le thermostat matériel reste indispensable. Pas de changement du bouclage.
