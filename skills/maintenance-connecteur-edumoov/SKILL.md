---
name: "maintenance-connecteur-edumoov"
description: "Modifier, tester, valider en réel, pousser et documenter le connecteur MCP Edumoov (dépôt ~/dev/edumoov-mcp) : outils, garde-fou d'écriture, tests, déploiement Azure, specs du projet."
---

# Maintenance du connecteur MCP Edumoov

À utiliser pour toute évolution du serveur MCP `edumoov` (Educartable, Livret, Journal, Appel, Cartable…) : nouvel outil de lecture ou d'écriture, correctif, découverte d'un endpoint, revérification d'un comportement Edumoov.

## Contexte à relire d'abord (projet Claude)

- `claude/spec-connecteur-mcp-edumoov.md` (auth, outils de lecture, sécurité, Copilot/Azure §8, incidents §9).
- `claude/spec-ecriture-edumoov.md` (écriture : méthodes, garde-fou, outils, pièces jointes §6.11, reste à faire).
- `claude/cartographie-edumoov.md` (rétro-ingénierie de l'API).

## Où et comment travailler

- Dépôt sur le Mac de Rémi : `/Users/remi/dev/edumoov-mcp` (venv `.venv`, installation éditable). Travailler sur place via Desktop Commander, jamais sur une copie dans le cloud. Ne pas confondre avec `~/dev/edumoov-mcp-prototype` (ancien dépôt).
- Commencer par `git fetch` + `git status -sb` ; une branche par changement non trivial.
- Modules : `client.py` (transport RPC/REST, jobs orchestrateur, téléchargement et envoi de fichiers), `server.py` (outils de lecture, instructions MCP, imports en fin de fichier), `school_tools.py` (Appel en lecture, registres et feuilles d'appel, licences), `writes.py` (`WriteGate`, listes blanches), `write_tools.py` (annonces et leurs pièces jointes, réglages, classes, confirm/cancel, `check_attachment`), `cartable_write_tools.py` (cahier de liaison et ses pièces jointes, commentaires), `family_codes.py` (fiches PDF des codes familles, modèle Educartable, fpdf2), `appeals.py` (logique pure de l'appel : lignes du payload, motifs, rattachement des noms) et `appeal_write_tools.py` (appel d'une demi-journée, suppression), `scripts/appel_lot.py` (chargement d'un lot d'appels, essai à blanc par défaut).
- Opérations longues (registres, PDF) : la méthode RPC renvoie un job (`downloadRegisters` : HTTP 202 `{success, job}` ; `<scope>.jobs.tempPdf`, PDF serveur d'une page de l'app : HTTP 200 `{success, data}`) ; suivre avec `user.jobs.get {id}` (3-6 en cours, 7 terminé, sinon erreur) ; fichier = `result.download.url` (URL signée `filerz.edumoov.com`, 12 h, sans authentification).
- Envoi de fichiers (pièces jointes) : obtenir une signature puis POST multipart sur `filerz.edumoov.com` (`sig`, `payload`, `file`). Annonce : RPC `school.medias.url` (lien primaire `{model:"Message", key}` dans le payload signé). Cahier : REST `GET core/classroom/{id}/medias/url`, lien primaire en champs du formulaire (`model="HomeworkMessage"`, `key`). Transport `upload` du `WriteGate`, méthodes en liste blanche `ALLOWED_UPLOAD_METHODS` (nom pseudo-RPC `core.classroom.medias.url` pour le REST), revérifiée dans `client.upload_media`.
- Transport : RPC `POST api.edumoov.com/rpc/<scope>.<entité>.<action>` avec corps `{params, payload}` ; REST legacy `www.edumoov.com/api/1.0/...` avec `X-Edumoov-Nosession: true`. Pagination RPC : toujours passer `page`/`limit` explicites ou `_rpc_all_pages` (défaut silencieux à 10 lignes).
- Découvrir une méthode : lire le bundle front public (`app.edumoov.com/assets/index-*.js`, ou `static.edumoov.com/cartable` — `app.*.js` + chunks `c_*.js`) ; nom RPC = `<scope.model>.<entité ou endpointName>.<action>`. Côté Cartable, chercher l'`apiConfig` du modèle (`urlTpl`, `actions`) : `oldEndpoint:true` = REST legacy `www.edumoov.com/api/1.0` ; puis l'usage dans les composants (ex. `additionalFields` de l'uploader). Bundles et scripts d'exploration hors dépôt, supprimés après usage.

## Règles de sécurité non négociables

- Codes familles (`pupils/codes`) : uniquement par `client.fetch_family_codes` pour l'outil local `edumoov_family_codes_pdf` (décision de Rémi du 07/10/2026 : ces codes sont destinés aux familles) — local seulement (`EDUMOOV_DOWNLOAD_DIR` + `EDUMOOV_FAMILY_CODES=1`), une fiche PDF par élève (600), codes jamais renvoyés ni journalisés ni recopiés dans une conversation ; le marqueur reste bloqué dans `rest_get`/`rest_write`. Jamais le champ `password` de `classroom.pupils.fetch` ; `ine`/`birthday` redactés par défaut.
- Toute écriture passe par `WriteGate` : outil `*_prepare_*` (aperçu + jeton, aucune écriture) puis `edumoov_write_confirm`. Ajouter chaque nouvelle méthode à `ALLOWED_WRITE_METHODS` (RPC), `ALLOWED_REST_WRITES` (REST) ou `ALLOWED_UPLOAD_METHODS` (envoi de fichier), revérifiées dans `client.rpc_write` / `client.rest_write` / `client.upload_media`. Référencer le gate via `write_tools._gate` (jamais une copie importée) pour partager les jetons.
- Pièces jointes : fichiers locaux sous `EDUMOOV_ATTACH_ROOTS` (défaut `~/Charlemagne`), extensions en liste blanche, documents bancaires refusés, fichier recontrôlé à la confirmation ; hôte d'envoi limité à `*.edumoov.com` en https.
- Appel (registre réglementaire) : écriture exposée depuis le 04/10/2026, uniquement via `edumoov_appeal_prepare_*` + confirmation ou `scripts/appel_lot.py --confirm`, après accord explicite de Rémi ; toujours un essai à blanc puis un test sur une classe et une journée avant un lot. Jamais exposés : `school.pupilsappeals.update` seul, `batchDelete`, `signatureFile`.
- Téléchargements : URL signées jamais renvoyées au client ni journalisées (filtre sur le logger `httpx`) ; fichiers enregistrés seulement dans des sous-dossiers de `EDUMOOV_DOWNLOAD_DIR` (`registres/`, `appels/` ; droits 600, jamais d'écrasement) ; outils refusés si la variable n'est pas définie (serveur distant) ; chemins de PDF en liste blanche dans `client.start_pdf_job` (`/appeals/<date>/<color|grey|empty>`, portées école/classe).
- Corps des messages : l'interface Educartable affiche du **HTML** (annonces comme cahier de liaison). Tout texte saisi par Claude est converti avant envoi — `write_tools.text_to_html` (annonces, `<p>`/`<br>`) et `cartable_write_tools.cahier_body_html` (cahier, format de l'éditeur : première ligne nue puis `<div>` par ligne, `<div><br></div>` pour une ligne vide), échappement HTML, `body_is_html=True` pour passer du HTML tel quel. Incident du 08/10/2026 : cahier envoyé en texte brut → message en un seul bloc, 15 brouillons remis en forme à la main par la direction. Tout nouvel outil qui écrit un corps doit faire de même et le vérifier en réel (relire le `body` stocké).
- Logs sur stderr uniquement (stdout = JSON-RPC), sans contenu de message ni jeton.
- En `streamable-http` (Azure), le serveur refuse de démarrer si `EDUMOOV_ENABLE_WRITES=1` : ne jamais lever ce verrou tant que l'authentification par utilisateur (spec §8.2) n'existe pas.
- Jamais de données réelles d'élèves, de familles ou d'enseignants dans un fichier versionné (y compris fichiers générés à partir de captures). Dépôt public : aucun chemin de serveur propre à l'école.

## Tests

- `.venv/bin/python -m pytest -q` : tous verts avant commit. Tests hors ligne (respx, `tests/_helpers.fake_auth`) pour chaque garde-fou : refus hors liste blanche, payload confirmé = payload prévisualisé, refus sans destinataire, périmètre école/classe, champs multipart envoyés. `SETTINGS` est un dataclass gelé : le patcher avec `dataclasses.replace`.
- Vérifier que le serveur charge : compter les outils avec `asyncio.run(mcp.list_tools())`.
- Validation réelle : charger l'`env` du serveur depuis `~/Library/Application Support/Claude/claude_desktop_config.json` (`mcpServers.edumoov.env`), puis script temporaire `.venv/bin/python` qui importe d'abord `edumoov_mcp.server` (ordre d'import) et appelle les fonctions d'outils (`cartable_write_tools`, `write_tools.edumoov_write_confirm`, `server._get_client()`). N'afficher que des clés, statuts, compteurs et types — jamais de contenu de message ni de nom d'élève.
- Écriture réelle : uniquement sur un **brouillon sans aucun destinataire** (classe de test 44571, `pupil_ids=[]`), suppression dans un `finally`, puis vérification de l'absence. Exception : l'appel, qui n'a pas de brouillon — test réel sur une demi-journée passée d'une classe, avec l'accord de Rémi, puis relecture (`edumoov_appeals_list`). Jamais de publication réelle ni d'action touchant une famille sans accord explicite de Rémi pour ce cas précis. Supprimer le script temporaire ensuite.

## Commit, push, déploiement

- Commit et push **uniquement quand Rémi le demande**. Commit en français, corps listant les changements et la validation réelle, puis les lignes d'attribution indiquées par la session courante.
- Commit depuis le Mac (Desktop Commander) : l'outil peut refuser une commande dont le message de commit est écrit dedans (« Command not allowed », constaté le 08/10/2026 avec un message en heredoc contenant des balises `<div>`) — écrire le message dans un fichier temporaire (`write_file`), puis `git commit -F <fichier>`, et supprimer le fichier.
- Fusion `--ff-only` dans `master` (`github.com/aghulas/edumoov-mcp`), push, suppression de la branche. Le push déclenche le workflow « Build and deploy Python app to Azure Web App - edumoov-mcp-fontainebleau ». Suivre avec `gh run list --limit 1` (depuis le Mac) jusqu'à `success` (plusieurs minutes).
- Rappeler à Rémi de **quitter (Cmd+Q) et relancer Claude Desktop** : le processus MCP ne recharge pas le code à chaud.
- Toute édition de `claude_desktop_config.json` : sauvegarde horodatée avant, validation JSON après (incident du 01/10/2026 : accolade en trop).

## Documentation à tenir à jour

- `README.md` du dépôt (tableaux d'outils, colonne « validé en réel »), docstrings des outils (lus par Claude), instructions du serveur dans `server.py` si le périmètre d'écriture change.
- Specs du projet (`project_read`, modifier, `project_write` du fichier entier) : statut + dernier commit + nombre d'outils/tests en tête ; tableau des méthodes et validation ; « Reste à faire » ; incidents et enseignements.
- Si l'usage change, mettre à jour aussi la skill `edumoov-communication-familles`.

## Skills et GitHub

Chaque skill chargée dans Claude a sa source dans le dossier `skills/` d'un dépôt :
- `edumoov-mcp` (public) : edumoov-communication-familles, edumoov-controle-rentree, maintenance-connecteur-edumoov ;
- `ecoledirecte-admin-mcp` (public) : ecoledirecte-communication-familles, ecoledirecte-identifiants-familles, ecoledirecte-parametrage, maintenance-connecteur-ecoledirecte ;
- `charlemagne-mcp` (public) : charlemagne-affectation-photos, charlemagne-formats-import-aplim, charlemagne-import-infos-complementaires, charlemagne-import-mail-telephone ;
- `charlemagne-tools` (privé, skills nominatives) : charlemagne-facturation, charlemagne-import-emploi-du-temps, charlemagne-import-frais-facturation, charlemagne-serveur-aplim, charlemagne-personnel-annuaire, ecoledirecte-appel-primaire, edumoov-appel, fiches-forfaits-rentree, scans-documents-eleves.

Une skill se modifie par une carte de proposition ; une fois qu'elle est enregistrée par Rémi, recopier le SKILL.md enregistré (copie synchronisée de la session) dans le dépôt, commiter et pousser, puis vérifier que les deux versions sont identiques (empreinte du corps). Une skill avec des données nominatives ou des chemins de serveur de l'école va dans `charlemagne-tools`, jamais dans un dépôt public. Vers le Mac : Desktop Commander `write_file` (créer le dossier avant ; mode `rewrite` pour remplacer un fichier existant).
