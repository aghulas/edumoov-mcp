# edumoov-mcp (prototype)

Serveur MCP en lecture seule vers des données Edumoov (école [ecole],
[ville]), basé sur l'API non documentée identifiée par rétro-ingénierie —
voir `../docs/` et les docs `cartographie-edumoov.md` /
`spec-connecteur-mcp-edumoov.md` du projet Claude "Edumoov" pour le contexte complet.

**Statut : prototype personnel.** Pas d'accès officiel Edumoov à ce stade (démarche en
cours en parallèle). À usage restreint, volume de données modéré — voir les
avertissements CGU / droit des bases de données dans la cartographie.

## Installation

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate   # ou l'équivalent avec ton outil habituel
pip install -e .
```

(Si `venv` échoue sur ta machine comme ça a été le cas dans mon bac à sable de capture,
utilise `pip install --user -e .` à la place.)

## Authentification (prototype)

Ce prototype ne fait pas (encore) le login interactif PKCE — voir
`spec-connecteur-mcp-edumoov.md` §2 pour pourquoi (on ne sait pas encore si Keycloak
accepte un `redirect_uri` de callback local pour le client `apps`/`edumoov`, à tester).

En attendant, il part d'un **refresh_token** récupéré manuellement depuis une session
navigateur déjà authentifiée sur `app.edumoov.com` :

1. Connecte-toi normalement sur `app.edumoov.com` dans ton navigateur.
2. Ouvre les DevTools → onglet Réseau (ou Application → stockage local, selon où
   Edumoov range le token côté client) et récupère le `refresh_token` associé à la
   session Keycloak (même méthode que tu utilisais avec Postman/Burp).
3. Enregistre-le dans le store local du prototype :

```bash
python -m edumoov_mcp.auth "<refresh_token>"
```

Ça écrit `~/.edumoov-mcp/token.json` (permissions 600, jamais dans le dépôt — voir
`.gitignore`). Le serveur rafraîchit ensuite automatiquement l'access_token à chaque
appel qui en a besoin. **Le refresh_token finira par expirer** (durée non connue,
Keycloak) — si tu vois une erreur "Rafraîchissement du token refusé", relance cette
étape avec un refresh_token frais.

## Lancer le serveur

```bash
python -m edumoov_mcp
```

Le serveur parle MCP en stdio — à enregistrer comme n'importe quel serveur MCP local
dans Claude Desktop / Claude Code (config `mcpServers`, commande =
`python -m edumoov_mcp` avec le bon `cwd`/`PYTHONPATH`, ou `edumoov-mcp` directement
si installé avec `pip install -e .`).

## Outils disponibles (v0)

- `edumoov_classroom_get(classroom_id)` — fiche complète d'une classe.
- `edumoov_classroom_pupils_list(classroom_id, include_sensitive_fields=False)` —
  liste des élèves, INE/date de naissance retirés par défaut.
- `edumoov_cartable_items_list(classroom_id, types=[...], box=..., archived=...,
  pupil_id=..., achieved=[...], ...)` — endpoint unifié du cartable (liaison, devoirs,
  activités, RDV parents, suivi, archives — voir la cartographie §4 pour les
  combinaisons de filtres).

Voir `spec-connecteur-mcp-edumoov.md` §3 pour la liste complète des outils prévus
(non encore implémentés) et §4 pour les règles de sécurité appliquées.

## Premiers pas / à vérifier après le premier appel réel

1. **Champs sensibles de `pupils`** : `config.py` liste des noms de champs
   (`ine`, `date_naissance`...) à retirer par défaut, mais c'est une **hypothèse non
   vérifiée** — la capture réseau n'a jamais réussi à décoder le JSON réel de cet
   endpoint (bug mitmproxy documenté dans la cartographie §6.1). Après le premier
   appel réussi avec `include_sensitive_fields=True`, regarde les clés réelles et
   corrige `SETTINGS.sensitive_pupil_fields` en conséquence.
2. **Décodage REST** : si `edumoov_classroom_pupils_list` ou
   `edumoov_cartable_items_list` lèvent une erreur "réponse non décodable en JSON",
   c'est que le problème de la capture (probable absence de support brotli côté
   mitmproxy) existe aussi côté API réelle — remonte-le, ce serait une vraie
   découverte à documenter.
3. **Durée de vie du refresh_token** : note combien de temps il tient avant de devoir
   être régénéré, pour décider si ça vaut le coup d'investir dans le flow PKCE complet.

## Ce que ce prototype ne fait volontairement pas

- Aucune écriture (pas de création d'annonce, réponse à un message, validation
  d'appel...).
- Ne rafraîchit jamais l'endpoint `POST .../pupils/codes` (codes d'accès individuels
  des élèves) — bloqué au niveau du client (`client.py`), pas seulement par
  l'absence d'outil MCP.
- Ne stocke aucune donnée élève/famille au-delà du cache mémoire d'une requête.
