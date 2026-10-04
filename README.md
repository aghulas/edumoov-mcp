# edumoov-mcp (prototype)

Serveur MCP (lecture, et écriture encadrée — voir « Écriture ») vers des données Edumoov, basé sur l'API non
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

## Écriture (depuis le 30/09/2026)

Désactivée par défaut. Pour l'activer en local (stdio uniquement — refusé en
streamable-http) : `EDUMOOV_ENABLE_WRITES=1` dans l'`env` du serveur MCP, puis
redémarrer le client MCP.

Toujours en deux temps : un outil `*_prepare_*` vérifie la demande, renvoie un aperçu
(avant → après, destinataires, avertissements) et un jeton à usage unique (10 min,
`EDUMOOV_WRITE_CONFIRMATION_TTL`) ; seul `edumoov_write_confirm(jeton)` envoie l'appel
— exactement celui prévisualisé. `edumoov_write_cancel`, `edumoov_write_pending_list`.

| Outil de préparation | Méthode RPC | Validé en réel |
|---|---|---|
| `edumoov_advert_prepare_create` (brouillon par défaut, `publication="now"` ou date) | `school.messages.create` | oui (brouillon sans destinataire) |
| `edumoov_advert_prepare_update` (titre, corps, classes) | `school.messages.update` | oui |
| `edumoov_advert_prepare_visibility` (publish / schedule / unpublish) | `school.messages.update` | même méthode ; publication réelle non testée |
| `edumoov_advert_prepare_delete` | `school.messages.delete` | oui |
| `edumoov_settings_prepare_set` (user / classroom / school, fusion partielle) | `<scope>.settings.set` | user : aller-retour ; classroom : no-op ; school : non |
| `edumoov_classroom_prepare_update` (name, inc, cartable_activated…) | `school.classrooms.update` | no-op |
| `edumoov_classroom_prepare_teacher` (link / unlink) | `classroom.classrooms.link/unlink` | **non** |

Lecture associée : `edumoov_adverts_list`, `edumoov_settings_get`.
Garde-fous : liste blanche des méthodes RPC (`writes.py`, revérifiée dans
`client.rpc_write`), chemin `signatureFile` jamais modifiable, champs de classe en
liste blanche, logs sur stderr sans contenu. Tests : `tests/test_writes.py`.

## Lecture Direction ajoutée le 01/10/2026

- `edumoov_appeals_stats(date, period="day"|"month", classroom_id)` et
  `edumoov_appeals_list(start, stop, classroom_id)` — app Appel
  (`school.pupilsappeals.stats` / `.fetch`). Écriture : voir « Appel (écriture,
  04/10/2026) » plus bas.
- `edumoov_subscriptions_list(active_only=True)` — licences Livret / Cartable /
  Journal par classe ou enseignant, jours avant expiration, facture associée
  (`school.subscriptions.fetch`) ; codes d'accès jamais renvoyés.
- `edumoov_media_get_url` : `token` obligatoire pour les médias à id numérique
  depuis le correctif Edumoov du 16/09/2026 (sinon 404, message explicite).

## Cahier de liaison et commentaires (écriture, 01/10/2026)

Module `cartable_write_tools.py`, même garde-fou que les annonces (aperçu + jeton,
`edumoov_write_confirm`), écritures REST du Cartable via `client.rest_write` (liste
blanche `writes.ALLOWED_REST_WRITES`).

| Outil de préparation | Appel REST | Validé en réel (01/10, brouillon sans destinataire, classe 44571) |
|---|---|---|
| `edumoov_cahier_prepare_create` — toujours en **brouillon** ; `pupil_ids` (None = toute la classe), `message_type` info/alert, `commentable`, `acknowledgement` none/read/comment | `POST cartable/classroom/{id}/messages` | oui |
| `edumoov_cahier_prepare_update` — titre, corps, destinataires, commentaires, accusé (PUT partiel) | `PUT …/messages/{uuid}` | oui (champs non envoyés conservés) |
| `edumoov_cahier_prepare_visibility` — publish / unpublish (`{visible}`) | `PUT …/messages/{uuid}` | refus sans destinataire vérifié ; publication réelle non testée (notifierait les familles) |
| `edumoov_cahier_prepare_delete` | `DELETE …/messages/{uuid}` | oui |
| `edumoov_comment_prepare_create` — commentaire ou réponse (`reply_to_comment_id`) | `POST core/classroom/{id}/comments` | oui (commentaire + réponse) |
| `edumoov_comment_prepare_delete` | `DELETE core/classroom/{id}/comments/{id}/trash` | oui (suppression « douce » : `deleted=true` reste dans le fil) |

Garde-fous : classe active de l'école uniquement, destinataires limités aux élèves de
la classe, message vérifié comme appartenant à la classe (`scope_key`), publication
refusée sans destinataire, avertissement explicite (nombre d'élèves notifiés) pour
toute publication ou modification d'un message déjà publié, retour de
`edumoov_write_confirm` sans contenu (id, titre, statut). Tests :
`tests/test_cartable_writes.py`.

## Appel (écriture, 04/10/2026)

Modules `appeals.py` (logique pure : lignes du payload, motifs, rattachement des noms)
et `appeal_write_tools.py`, même garde-fou que les annonces. Méthodes lues dans le
bundle front (entité `appeals`, `endpointName: pupilsappeals`). L'appel a valeur de
registre : chaque aperçu le rappelle.

| Outil / script | Méthode RPC | Validé en réel |
|---|---|---|
| `edumoov_appeal_prepare_halfday(classroom_id, date, period am/pm, absent_pupil_ids, justification, eat/study/play_pupil_ids)` — création ou mise à jour d'une demi-journée (tous présents sauf absents ; pointages cantine / étude / périscolaire ; retards, motifs et pointages déjà saisis conservés ; élèves exclus de l'appel de la classe respectés ; date future refusée) | `classroom.pupilsappeals.batchUpsert` (params `{classroom_id}`, payload `{data, globalData: {date, pm, classroom_id}}`) | oui (04/10, appels de septembre 2026 des 15 classes) |
| `edumoov_appeal_prepare_reset(classroom_id, date, period)` — supprimer l'appel d'une demi-journée | `classroom.pupilsappeals.resetDay` (`{classroom_id, date, am\|pm: true}`) | non |
| `scripts/appel_lot.py lot.json [--confirm] [--remplacer]` — lot (ex. un mois saisi sur papier) : essai à blanc par défaut, noms rattachés aux élèves Edumoov (blocage si introuvable ou ambigu), demi-journées déjà saisies laissées telles quelles sauf `--remplacer`, journal sans nom d'élève | `classroom.pupilsappeals.batchUpsert` | oui (04/10) |

Motifs d'absence : `pendingjustification` (À justifier, défaut du front),
`unjustified`, `justified` (Motif légitime), `justifiedbut` (Autre motif). Hors liste
blanche : justification seule (`school.pupilsappeals.update`). Tests :
`tests/test_appeals.py`.

### Registres d'appel (téléchargement, 04/10/2026)

`edumoov_registers_download(months, classroom_ids=None, types=None, color=True,
extract=False)` — équivalent de Direction → Appel → Registres (« Télécharger pour
toutes les classes », ou une seule classe via `classroom_ids`). Lance
`school.pupilsappeals.downloadRegisters` (payload `{types, months, classroomIds,
color}`, réponse HTTP 202 avec un job orchestrateur), suit le job par `user.jobs.get`
(statut 7 = terminé) puis télécharge l'archive zip produite (un PDF par type —
appel, cantine, étude, périscolaire — et par mois). Ne modifie aucune donnée.

- Fichier enregistré dans `EDUMOOV_DOWNLOAD_DIR` (obligatoire : sans lui l'outil
  refuse, donc inactif sur un serveur distant), droits 600, jamais écrasé ;
  `extract=True` décompresse aussi l'archive (chemins de l'archive vérifiés).
- L'URL de téléchargement (signée, valable 12 h sans authentification) n'est jamais
  renvoyée et sa signature est masquée dans les journaux httpx ; hôte limité à
  `*.edumoov.com`.
- Registres en couleur par défaut (choix de l'école) ; `color=False` pour le noir et
  blanc, fichier suffixé `_nb`.
- Délai d'attente du job : `EDUMOOV_JOB_TIMEOUT` (180 s par défaut).
- Validé en réel le 04/10/2026 (une classe et les 15 classes, septembre 2026). Tests :
  `tests/test_registers.py`.

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

- Aucune écriture directe : tout passe par aperçu + confirmation (voir « Écriture »).
  Pas de justification d'absence seule.
- Ne rafraîchit jamais l'endpoint `POST .../pupils/codes` (codes d'accès individuels
  des élèves) — bloqué au niveau du client (`client.py`), pas seulement par
  l'absence d'outil MCP.
- Ne stocke aucune donnée élève/famille au-delà du cache mémoire d'une requête.
