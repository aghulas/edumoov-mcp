---
name: "maintenance-connecteur-edumoov"
description: "Modifier, tester, valider en réel, pousser et documenter le connecteur MCP Edumoov (dépôt ~/dev/edumoov-mcp) : outils, garde-fou d'écriture, tests, déploiement Azure, specs du projet."
---

# Maintenance du connecteur MCP Edumoov

À utiliser pour toute évolution du serveur MCP `edumoov` (Educartable, Livret, Journal, Appel, Cartable…) : nouvel outil de lecture ou d'écriture, correctif, découverte d'un endpoint, revérification d'un comportement Edumoov.

## Contexte à relire d'abord (projet Claude)

- `claude/spec-connecteur-mcp-edumoov.md` (auth, outils de lecture, sécurité, Copilot/Azure §8, incidents §9).
- `claude/spec-ecriture-edumoov.md` (écriture : méthodes, garde-fou, outils, reste à faire).
- `claude/cartographie-edumoov.md` (rétro-ingénierie de l'API).

## Où et comment travailler

- Dépôt sur le Mac de Rémi : `/Users/remi/dev/edumoov-mcp` (venv `.venv`, installation éditable). Travailler sur place via Desktop Commander, jamais sur une copie dans le cloud. Ne pas confondre avec `~/dev/edumoov-mcp-prototype` (ancien dépôt).
- Commencer par `git fetch` + `git status -sb`.
- Modules : `client.py` (transport RPC/REST, jobs orchestrateur, téléchargement de fichiers), `server.py` (outils de lecture, instructions MCP, imports en fin de fichier), `school_tools.py` (Appel en lecture, registres et feuilles d'appel, licences), `writes.py` (`WriteGate`, listes blanches), `write_tools.py` (annonces, réglages, classes, confirm/cancel), `cartable_write_tools.py` (cahier de liaison, commentaires), `appeals.py` (logique pure de l'appel : lignes du payload, motifs, rattachement des noms) et `appeal_write_tools.py` (appel d'une demi-journée, suppression), `scripts/appel_lot.py` (chargement d'un lot d'appels, essai à blanc par défaut).
- Opérations longues (registres, PDF) : la méthode RPC renvoie un job (`downloadRegisters` : HTTP 202 `{success, job}` ; `<scope>.jobs.tempPdf`, PDF serveur d'une page de l'app : HTTP 200 `{success, data}`) ; suivre avec `user.jobs.get {id}` (3-6 en cours, 7 terminé, sinon erreur) ; fichier = `result.download.url` (URL signée `filerz.edumoov.com`, 12 h, sans authentification).
- Transport : RPC `POST api.edumoov.com/rpc/<scope>.<entité>.<action>` avec corps `{params, payload}` ; REST legacy `www.edumoov.com/api/1.0/...` avec `X-Edumoov-Nosession: true`. Pagination RPC : toujours passer `page`/`limit` explicites ou `_rpc_all_pages` (défaut silencieux à 10 lignes).
- Découvrir une méthode : lire le bundle front public (`app.edumoov.com/assets/index-*.js`, ou `static.edumoov.com/cartable` pour le Cartable) ; nom = `<scope.model>.<entité ou endpointName>.<action>`. Bundles et scripts d'exploration hors dépôt, supprimés après usage.

## Règles de sécurité non négociables

- Jamais d'outil exposant `pupils/codes` ni le champ `password` des élèves ; `ine`/`birthday` redactés par défaut.
- Toute écriture passe par `WriteGate` : outil `*_prepare_*` (aperçu + jeton, aucune écriture) puis `edumoov_write_confirm`. Ajouter chaque nouvelle méthode à `ALLOWED_WRITE_METHODS` (RPC) ou `ALLOWED_REST_WRITES` (REST), revérifiées dans `client.rpc_write` / `client.rest_write`. Référencer le gate via `write_tools._gate` (jamais une copie importée) pour partager les jetons.
- Appel (registre réglementaire) : écriture exposée depuis le 04/10/2026, uniquement via `edumoov_appeal_prepare_*` + confirmation ou `scripts/appel_lot.py --confirm`, après accord explicite de Rémi ; toujours un essai à blanc puis un test sur une classe et une journée avant un lot. Jamais exposés : `school.pupilsappeals.update` seul, `batchDelete`, `signatureFile`.
- Téléchargements : URL signées jamais renvoyées au client ni journalisées (filtre sur le logger `httpx`) ; fichiers enregistrés seulement dans des sous-dossiers de `EDUMOOV_DOWNLOAD_DIR` (`registres/`, `appels/` ; droits 600, jamais d'écrasement) ; outils refusés si la variable n'est pas définie (serveur distant) ; chemins de PDF en liste blanche dans `client.start_pdf_job` (`/appeals/<date>/<color|grey|empty>`, portées école/classe).
- Logs sur stderr uniquement (stdout = JSON-RPC), sans contenu de message ni jeton.
- En `streamable-http` (Azure), le serveur refuse de démarrer si `EDUMOOV_ENABLE_WRITES=1` : ne jamais lever ce verrou tant que l'authentification par utilisateur (spec §8.2) n'existe pas.
- Jamais de données réelles d'élèves, de familles ou d'enseignants dans un fichier versionné (y compris fichiers générés à partir de captures).

## Tests

- `.venv/bin/python -m pytest -q` : tous verts avant commit. Tests hors ligne (respx, `tests/_helpers.fake_auth`) pour chaque garde-fou : refus hors liste blanche, payload confirmé = payload prévisualisé, refus sans destinataire, périmètre école/classe. `SETTINGS` est un dataclass gelé : le patcher avec `dataclasses.replace`.
- Vérifier que le serveur charge : compter les outils avec `asyncio.run(mcp.list_tools())`.
- Validation réelle : charger l'`env` du serveur depuis `~/Library/Application Support/Claude/claude_desktop_config.json` (`mcpServers.edumoov.env`), puis script temporaire `.venv/bin/python` qui appelle les fonctions d'outils. N'afficher que des clés, statuts, compteurs et types — jamais de contenu de message ni de nom d'élève.
- Écriture réelle : uniquement sur un **brouillon sans aucun destinataire** (classe de test 44571), puis suppression et vérification de l'absence. Exception : l'appel, qui n'a pas de brouillon — test réel sur une demi-journée passée d'une classe, avec l'accord de Rémi, puis relecture (`edumoov_appeals_list`). Jamais de publication réelle ni d'action touchant une famille sans accord explicite de Rémi pour ce cas précis. Supprimer le script temporaire ensuite.

## Commit, push, déploiement

- Commit en français, corps listant les changements et la validation réelle, puis les lignes d'attribution indiquées par la session courante.
- Push sur `master` (`github.com/aghulas/edumoov-mcp`) : déclenche le workflow « Build and deploy Python app to Azure Web App - edumoov-mcp-fontainebleau ». Suivre avec `gh run list --limit 1` jusqu'à `success` (~2 min).
- Rappeler à Rémi de **quitter (Cmd+Q) et relancer Claude Desktop** : le processus MCP ne recharge pas le code à chaud.
- Toute édition de `claude_desktop_config.json` : sauvegarde horodatée avant, validation JSON après (incident du 01/10/2026 : accolade en trop).

## Documentation à tenir à jour

- `README.md` du dépôt (tableaux d'outils, colonne « validé en réel »), docstrings des outils (lus par Claude), instructions du serveur dans `server.py` si le périmètre d'écriture change.
- Specs du projet (`project_read`, modifier, `project_write` du fichier entier) : statut + dernier commit + nombre d'outils/tests en tête ; tableau des méthodes et validation ; « Reste à faire » ; incidents et enseignements.