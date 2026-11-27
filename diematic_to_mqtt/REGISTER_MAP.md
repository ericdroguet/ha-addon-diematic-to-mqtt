# Diematic 3 — vérification du 27 novembre 2026

Installation GT226, contrôleur CTRL 1422, M2 : OUI. Comparaison des valeurs Modbus 1.1.10/1.1.11 et des photos du tableau installateur. Une concordance statique confirme le décodage avec la table, pas une autorisation d'écriture ni une preuve d'alias.

| Adresse | Mot brut | Affichage panneau | Statut |
|---|---:|---|---|
| 11 | 4 | TEMPO P.CHAUFF 4 min | concordant |
| 60 | 1 | ECS:RELATIVE | code 1 confirmé ; autres codes non testés |
| 61 | 4 | TEMPO P.ECS 4 min | concordant |
| 264 | 3 | INERTIE BATI 3 | concordant |
| 266 | 120 | LARG.BANDE V3V 12 °C | concordant |
| 268 | 0 | ANTILEG:NON | concordant |
| 269 | 1 | FCT.MIN.BRUL 1 min | concordant |
| 271 | 4 | TEMPO BRULEUR 4 min | concordant |
| 272 | 3 | TEMPO P.CHAUD. 3 min | concordant |
| 273 | 0 | DEL.CHAUD:NON | concordant |
| 426 | 40 | DEC.CHAUD/V3V 4 °C | concordant en lecture seule |
| 72 | 40 | non vérifié | différentiel candidat |
| 435 | 0 | non vérifié | brut, diagnostic candidat |
| 438 | 120 | LARG.B.CHAUD. non retrouvé | fonction non validée, alias possible du 266 ; échelle historique /10 conservée |
| 73,267,306 | 65535 | — | retirés |
| 265,274,277 | 1,0,0 | non vérifié | sondage ponctuel seulement |
| 278,454,604,622,623,625 | 65535 | — | indisponibles |

Autres observations du panneau : NUIT:ABAIS., ECS:CHAUDIERE. Adresses non authentifiées. 436 = 720 lors du sondage ; 620 déjà disponible, pas de nouveau doublon de consigne publié.

## Politique de commande
Aucune nouvelle commande en écriture en 1.1.12. Ne pas déduire l'écriture de la lecture. 678 seul conserve la procédure existante de contrôle. Éventuels réglages futurs à valider individuellement (documentation, lecture avant/après, limites constructeur, retour arrière, validation manuelle) ; aucun réglage automatique des paramètres installateur.

Version 1.1.13: essai manuel du seul registre 266 (4–16 K), lecture fraîche avant écriture unique, contrôle après, blocage en cas de valeur non confirmée. Aucun changement automatique. 438 reste en lecture seule et non identifié.

## Sources
- https://community.jeedom.com/uploads/short-url/luej1LlG3Wu6xGjpEOxCXbrPCoq.pdf
- https://community.jeedom.com/uploads/short-url/qjNF6pFIcvSXRka7EFug2vIEKZE.pdf
- Notice GT120/220 Diematic 3 réf. 300001509.
- Journaux locaux 1.1.11 et photos du panneau du 27 novembre 2026.
