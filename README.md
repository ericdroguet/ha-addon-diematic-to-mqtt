# Diematic to MQTT – application Home Assistant (avec circuit C)

🇫🇷 Français · [🇬🇧 English below](#english)

Application (add-on) Home Assistant qui relie une chaudière **De Dietrich équipée d'une régulation Diematic 3** (et Diematic 4 / Delta) à MQTT, via une passerelle ModBus RS485 ↔ TCP (ex. USR-W610). Les entités apparaissent automatiquement dans Home Assistant (MQTT discovery).

## Crédits

Ce projet est **entièrement basé sur le travail de [Benoit3](https://github.com/Benoit3)** :
👉 **[Benoit3/Diematic_to_MQTT](https://github.com/Benoit3/Diematic_to_MQTT)** (licence MIT, © 2021 Benoit3).

Tout le dialogue ModBus avec la Diematic (gestion maître/esclave, registres, écriture des modes) vient de son projet. Ce dépôt ne fait qu'ajouter un emballage « application Home Assistant » et quelques évolutions listées ci-dessous. Merci à lui !

La table des registres du circuit C provient de la documentation publique Sofrel S500 (registres Diematic 3).

## Ce que ce dépôt ajoute

| Évolution | Détail |
|---|---|
| **Application Home Assistant** | Installation depuis la boutique d'applications, toute la configuration dans l'onglet *Configuration* (plus de fichier `.conf` à éditer). |
| **Prise en charge du circuit C** | Mode (AUTO, TEMP/PERM JOUR, TEMP/PERM NUIT, ANTIGEL), consignes jour/nuit/antigel, température calculée, température de départ, pompe C. Option `enable_circuit_c`. |
| **Reconnexion automatique** | Si la passerelle ModBus est injoignable (coupure WiFi, redémarrage), l'application attend et réessaie toutes les 30 s au lieu de s'arrêter. |

## Installation

1. Dans Home Assistant : **Paramètres → Applications → Boutique des applications → ⋮ → Dépôts**.
2. Ajouter l'URL : `https://github.com/pebrb/ha-addon-diematic-to-mqtt`
3. Installer **Diematic to MQTT**, renseigner l'onglet *Configuration*, démarrer.

Prérequis : un broker MQTT (ex. application Mosquitto) et une passerelle RS485 ↔ TCP en mode transparent (9600 bauds, 8N1) raccordée au bus ModBus de la chaudière.

## Options

| Option | Description |
|---|---|
| `modbus_ip` / `modbus_port` | Adresse de la passerelle ModBus (ex. USR-W610, port 20108 par défaut) |
| `regulator_address` / `interface_address` | Adresses ModBus (par défaut `0x0A` / `0x32`) |
| `mqtt_broker_host`, `mqtt_broker_port`, `mqtt_broker_login`, `mqtt_broker_password` | Broker MQTT (`core-mosquitto` pour l'application Mosquitto) |
| `topic_prefix`, `client_id` | Topics publiés : `<topic_prefix>/<client_id>/...` |
| `regulator_type` | `Diematic3`, `Diematic4` ou `DiematicDelta` |
| `time_sync`, `timezone` | Synchronisation de l'horloge de la chaudière |
| `period` | Période de rafraîchissement (s) |
| `enable_circuit_a` / `_b` / `_c` | Force la publication des circuits A, B, C |
| `mqtt_discovery_enable`, `discovery_prefix` | Découverte automatique dans Home Assistant |

## Circuit C (Diematic 3)

| Donnée | Registre | Topic MQTT (`<prefix>/<client_id>/...`) |
|---|---|---|
| Consigne jour | 35 | `zoneC/dayTemp` (+ `/set`) |
| Consigne nuit | 36 | `zoneC/nightTemp` (+ `/set`) |
| Consigne antigel | 37 | `zoneC/antiiceTemp` (+ `/set`) |
| Mode | 38 | `zoneC/mode` (+ `/set`) |
| Température ambiance | 39 | `zoneC/temp` |
| Température calculée | 44 | `zoneC/targetTemp` |
| Température départ | 45 | `zoneC/departureTemp` |
| Pompe C | 428, bit 5 | `zoneC/pump` |

Lors de l'écriture du mode, les bits eau chaude sanitaire du registre 38 sont préservés.

## ⚠️ Avertissements

- **Testé uniquement sur une De Dietrich MC45 avec Diematic III** (circuit C = radiateurs, circuit B = échangeur piscine, circuit A absent) et une passerelle USR-W610. Le circuit C n'a pas été testé sur d'autres modèles.
- **Vérifiez la configuration de votre chaudière** (menus installateur : CIRC. A / B / C) avant d'activer un circuit ou d'écrire un mode : sur certaines installations, un circuit pilote une piscine ou un plancher chauffant.
- Logiciel fourni « tel quel », sans garantie (voir [LICENSE](diematic_to_mqtt/LICENSE)). Toute écriture ModBus modifie le fonctionnement réel de la chaudière.

## Licence

MIT – © 2021 Benoit3 (projet d'origine). Les modifications de ce dépôt sont publiées sous la même licence.

Les évolutions de ce dépôt ont été développées avec l'aide de Claude (Anthropic).

---

<a name="english"></a>
## 🇬🇧 English

Home Assistant app (add-on) bridging a **De Dietrich boiler with a Diematic 3 controller** (also Diematic 4 / Delta) to MQTT through a ModBus RS485 ↔ TCP gateway (e.g. USR-W610), with Home Assistant MQTT discovery.

**Credits:** entirely based on **[Benoit3/Diematic_to_MQTT](https://github.com/Benoit3/Diematic_to_MQTT)** (MIT, © 2021 Benoit3), which does all the ModBus work. Thanks to him! Circuit C registers come from the public Sofrel S500 documentation.

**What this repository adds:**
- Packaging as a **Home Assistant app**, configured from the UI.
- **Circuit C support**: mode, day/night/anti-frost setpoints, calculated and departure temperatures, pump (`enable_circuit_c` option). See the register table above.
- **Automatic reconnection**: when the ModBus gateway is unreachable, the app retries every 30 s instead of stopping.

**Install:** Settings → Apps → App store → ⋮ → Repositories → add this repository URL → install *Diematic to MQTT* → configure → start.

**Warning:** only tested on a De Dietrich MC45 with Diematic III (circuit C = radiators, B = pool heat exchanger, A absent) and a USR-W610 gateway. Check your boiler's installer menus (CIRC. A/B/C) before enabling a circuit or writing a mode. Provided "as is", without warranty.

Changes in this repository were developed with the help of Claude (Anthropic).
