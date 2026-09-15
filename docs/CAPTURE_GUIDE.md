# Guide de capture — trafic web app.edumoov.com via mitmproxy

## 0. Rappel

Ceci capture le trafic réseau de **ton propre compte**, pendant que tu utilises
l'app normalement dans ton navigateur. La capture va contenir des données
personnelles réelles (élèves, familles, enseignants) même si l'addon réduit
automatiquement les tableaux à 2 exemples + le total. Garde le fichier
`captures/edumoov_capture.jsonl` dans ce dossier local, ne le partage pas
au-delà de ce qui est nécessaire, et supprime-le une fois le prototype/API
documenté.

## 1. Lancer mitmdump avec l'addon de capture

Dans un Terminal :

```bash
cd ~/dev/edumoov-mcp-prototype/scripts
mitmdump -s capture_addon.py \
  --set edumoov_out=$HOME/dev/edumoov-mcp-prototype/captures/edumoov_capture.jsonl \
  -p 8080
```

Laisse ce terminal ouvert pendant toute la session de navigation. Chaque
requête vers *.edumoov.com/*.edumoov.net (hors assets statiques) s'affiche
dans le terminal et s'ajoute au fichier `.jsonl`.

## 2. Configurer le proxy (le temps de la capture)

Le plus simple pour ne pas polluer tout ton trafic Mac : configure le proxy
uniquement sur le navigateur que tu utilises pour Edumoov, ou temporairement
au niveau système puis désactive-le après.

- **Système** : Réglages > Réseau > Wi-Fi > Détails > Proxys > coche
  "Serveur proxy web (HTTP)" et "Serveur proxy web sécurisé (HTTPS)" →
  `127.0.0.1` port `8080`. Pense à décocher après la capture.
- **Chrome en ligne de commande** (isolé, ne touche pas le reste du système) :
  ```bash
  open -na "Google Chrome" --args --proxy-server="127.0.0.1:8080" --user-data-dir="/tmp/chrome-mitm-profile"
  ```

## 3. Faire confiance au certificat mitmproxy (une seule fois)

Avec le proxy actif, va sur `http://mitm.it` dans le navigateur concerné et
installe/fais confiance au certificat macOS. (Si tu as déjà utilisé mitmproxy
avant sur cette machine — le dossier `~/.mitmproxy` existe déjà — c'est
peut-être déjà fait, vérifie dans Trousseau d'accès que le certificat
"mitmproxy" est en "Toujours faire confiance".)

## 4. Parcours à faire pendant la capture (checklist)

Pour couvrir un maximum d'endpoints en un seul passage, navigue dans cet
ordre (repris de la cartographie déjà faite) :

**Direction** (`app.edumoov.com/direction/school/11777/...`)
- [ ] Tableau de bord
- [ ] Classes & Enseignants
- [ ] Élèves et familles (attends le chargement complet)
- [ ] Appel — change de date (flèche gauche/droite), bascule Matin/Après-midi,
      bascule App./Reg., "Grouper par classe"
- [ ] Annonces d'école — ouvre le formulaire de création (sans forcément envoyer)
- [ ] Appréciations — clique sur une classe puis un élève
- [ ] Documents, export LSU — les 4 onglets (Périodiques / Cycles-attest. / Détails / Signatures)
- [ ] Vote & élections
- [ ] Licences et factures — les 3 vues (Classes / Enseignants / Expirées)
- [ ] Signature (préférences)

**Autres apps** (sélecteur en haut à droite)
- [ ] Classe — onglets élèves / groupes / collègues / licences
- [ ] Livret — onglets Accueil / Évals / Suivi / Appréc. / Élèves / Docs
- [ ] Cartable — messages (cahier de liaison), tasks (devoirs), activities (cahier de vie)
- [ ] Journal — onglets Mémo / Appel / Devoirs / Créneaux / Séances passées / Prep' / Répart.

Essaie aussi de faire une action qui écrit des données (ex. cocher un élève
présent puis valider l'appel sur UNE classe test) pour capturer un exemple de
requête POST/PUT, pas seulement des GET.

## 5. Arrêter et analyser

Ctrl+C dans le terminal mitmdump, puis :

```bash
python3 ~/dev/edumoov-mcp-prototype/scripts/analyze_capture.py \
  ~/dev/edumoov-mcp-prototype/captures/edumoov_capture.jsonl
```

Ça génère `docs/api_inventory.md` (lisible) et `docs/api_inventory.json`
(exploitable pour construire le connecteur MCP). Dis-moi quand c'est fait,
je peux lire directement ces fichiers depuis le dossier connecté pour la
suite (construire le connecteur / la spec MCP).
