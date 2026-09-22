# edumoov-mcp (prototype)

Serveur MCP en lecture seule vers des données Edumoov, basé sur l'API non
documentée identifiée par rétro-ingénierie —
voir `docs/` et les docs `cartographie-edumoov.md` /
`spec-connecteur-mcp-edumoov.md` du projet Claude "Edumoov" pour le contexte complet.

**Statut : prototype personnel.** Pas d'accès officiel Edumoov à ce stade (démarche en
cours en parallèle). À usage restreint, volume de données modéré — voir les
avertissements CGU / droit des bases de données dans la cartographie.

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate   # ou l'équivalent avec ton outil habituel
pip install -e .
```

(Le code vit à la racine du dépôt depuis le 22/09/2026 — il était auparavant dans
`mcp-server/`, déplacé pour que la structure soit identique à `charlemagne-mcp` et
`ecoledirecte-admin-mcp`, ce qu'attend la config par défaut du Deployment Center
Azure/GitHub Actions.)

(Si `venv` échoue sur ta machine comme ça a été le cas dans mon bac à sable de capture,
utilise `pip install --user -e .` à la place.)

## Authentification (prototype)

Ce prototype ne fait pas le login interactif PKCE — **testé et écarté le
16/09/2026** (voir `spec-connecteur-mcp-edumoov.md` §2) : Keycloak refuse
d'emblée tout `redirect_uri` de callback local pour le client `apps` (liste
blanche stricte limitée à `app.edumoov.com`/`app-beta.edumoov.com`). Cette voie
ne peut s'ouvrir que si Edumoov enregistre lui-même un `redirect_uri` dédié —
c'est désormais un point demandé dans la démarche officielle en cours.

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
appel qui en a besoin. **Le refresh_token finira par expirer** — durée observée en
conditions réelles le 16/09/2026 : **3h** (`refresh_expires_in: 10800`) — si tu vois
une erreur "Rafraîchissement du token refusé", relance cette étape avec un
refresh_token frais. C'est la principale limite du prototype tant que la démarche
officielle auprès d'Edumoov n'a pas abouti (voir §2 de la spec).

## Lancer le serveur

```bash
python -m edumoov_mcp
```

Le serveur parle MCP en stdio — à enregistrer comme n'importe quel serveur MCP local
dans Claude Desktop / Claude Code (config `mcpServers`, commande =
`python -m edumoov_mcp` avec le bon `cwd`/`PYTHONPATH`, ou `edumoov-mcp` directement
si installé avec `pip install -e .`).

### Déploiement distant (Azure App Service, streamable-http)

```bash
python -m edumoov_mcp --transport streamable-http --host 0.0.0.0 --port 8003
```

Nécessite les variables d'environnement `MCP_ENTRA_TENANT_ID`, `MCP_ENTRA_APP_ID_URI`
et optionnellement `MCP_ENTRA_ALLOWED_GROUP_ID` / `MCP_ENTRA_PUBLIC_URL` — voir le
dépôt partagé `mcp-entra-auth`. Web App Azure cible : `edumoov-mcp-fontainebleau`,
**port 8003** (`WEBSITES_PORT=8003`), Startup Command à poser dans Configuration →
Stack settings :

```
python -m edumoov_mcp --transport streamable-http --host 0.0.0.0 --port 8003
```

**⚠️ Rappel du blocage connu avant toute mise en prod distante** : la réauthentification
actuelle (`browser_auth.py`) pilote un navigateur Chromium non-headless — incompatible
tel quel avec un App Service headless. Voir `spec-connecteur-mcp-edumoov.md` §8 pour le
plan (démarrage en usage strictement personnel, jeton géré manuellement en attendant).

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Suite ajoutée le 16/09/2026 (23 tests au 16/09/2026, voir `tests/`), écrite en
réaction directe à l'incident de redaction silencieuse documenté dans
`cartographie-edumoov.md` §9 : plutôt que de re-vérifier au cas par cas que les
champs sensibles élèves sont bien retirés, ces propriétés sont désormais testées
automatiquement. Aucun test ne touche le réseau réel ni un token réel (RPC/REST
mockés via `respx`, auth factice) — la suite tourne hors ligne, sans dépendre du
refresh_token du moment.

- `tests/test_client_envelope.py` — dépaquetage de l'enveloppe REST
  (`_unwrap_rest_envelope`) et forme exacte de l'enveloppe RPC `{params, payload}`.
- `tests/test_security_guards.py` — `_check_allowed` (endpoint `pupils/codes`
  bloqué), et un garde-fou structurel qui échoue si `classroom.pupils.fetch`
  (variante RPC avec un champ `password`, jamais utilisée volontairement) est un
  jour appelée sans revue explicite.
- `tests/test_media_get_url.py` — comportement (atypique) de `core.medias.file` :
  jamais de header `Authorization` envoyé, gestion de la redirection 302.
- `tests/test_redaction.py` — `edumoov_classroom_pupils_list` retire bien
  `ine`/`birthday` par défaut, les inclut sur demande explicite, et refuse
  bruyamment (au lieu de renvoyer en silence) toute donnée qui ne serait pas une
  vraie liste — fermeture du trou exact qui avait causé l'incident du §9.
- `tests/test_journal.py` (ajouté le 16/09/2026) — fige le nom de méthode RPC
  exact et le domaine (`user.` vs `classroom.`) des 4 outils Journal, trouvés par
  essais successifs contre l'API réelle plutôt que documentés officiellement —
  voir cartographie §6.6. Le plus facile des tests de cette suite à casser
  silencieusement si quelqu'un "simplifie" le nommage plus tard.

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
