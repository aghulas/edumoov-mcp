"""Client HTTP vers les deux surfaces API d'Edumoov identifiées par capture réseau.

Voir cartographie-edumoov.md §6 pour le détail :
- RPC (api.edumoov.com/rpc/<domaine>.<ressource>.<action>) : données de structure
  (école, classe, enseignants, référentiels). Enveloppe {success, data, paging?}.
- REST classique (www.edumoov.com/api/1.0/...) : contenu du cartable (messages,
  devoirs, activités, RDV, suivi, archives — tous derrière le même endpoint
  /cartable/classroom/{id}/messages, filtré par query params).

Garde-fou de sécurité : `_FORBIDDEN_PATH_MARKERS` bloque au niveau du client, et pas
seulement par omission côté outils MCP, tout endpoint qui exposerait un secret
d'authentification élève (voir spec-connecteur-mcp-edumoov.md §3, dernière ligne).
"""
from __future__ import annotations

import datetime
from typing import Any, Iterable

import httpx

from .auth import KeycloakAuth
from .config import SETTINGS

# Jamais interrogé, quel que soit l'appelant : retourne les codes d'accès individuels
# des élèves (équivalent à un mot de passe de mineur). Voir cartographie §6.1 et
# spec §3 — aucun outil MCP ne doit jamais atteindre cet endpoint.
_FORBIDDEN_PATH_MARKERS: tuple[str, ...] = ("pupils/codes",)


class ForbiddenEndpointError(RuntimeError):
    """Levée si du code tente d'atteindre un endpoint explicitement exclu."""


class EdumoovApiError(RuntimeError):
    """Erreur renvoyée par l'API Edumoov (RPC ou REST)."""


def _check_allowed(path: str) -> None:
    for marker in _FORBIDDEN_PATH_MARKERS:
        if marker in path:
            raise ForbiddenEndpointError(
                f"Endpoint explicitement exclu du connecteur : '{path}' "
                "(voir cartographie-edumoov.md §6.1 — données d'authentification élève)."
            )


def _coerce_id(value: Any) -> str:
    """Normalise un id pour comparaison (l'API renvoie des int, nos outils MCP des str)."""
    return str(value)


def _unwrap_rest_envelope(payload: Any, *, context: str) -> list[dict[str, Any]]:
    """Dépaquette une enveloppe REST {success, data: [...], pagination: {...}}.

    Confirmé empiriquement (15/09/2026) sur DEUX endpoints indépendants
    (core/classroom/{id}/pupils ET cartable/classroom/{id}/messages) avec la même
    forme exacte — assez pour généraliser ce dépaquetage plutôt que de le refaire
    au cas par cas à chaque nouvel endpoint REST. Si un futur endpoint s'avère
    avoir une forme différente, cette fonction lèvera une erreur explicite plutôt
    que de renvoyer silencieusement autre chose qu'une liste (voir l'incident de
    redaction documenté dans list_pupils / cartography §6.1).
    """
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"] or []
    raise EdumoovApiError(
        f"{context} : réponse dans un format inattendu (enveloppe {{data:...}} non "
        f"reconnue) : {type(payload).__name__}. À vérifier manuellement avant de "
        "supposer que c'est une liste exploitable."
    )


class EdumoovClient:
    """Enveloppe fine autour des deux surfaces API. Un client par process suffit."""

    def __init__(self, auth: KeycloakAuth | None = None):
        self._auth = auth or KeycloakAuth()
        self._http = httpx.AsyncClient(timeout=20.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict[str, str]:
        token = await self._auth.get_access_token(self._http)
        return {"Authorization": f"Bearer {token}"}

    async def _rest_headers(self) -> dict[str, str]:
        """Headers pour la couche REST legacy (www.edumoov.com).

        `X-Edumoov-Nosession: true` est INDISPENSABLE ici (constat empirique du
        15/09/2026, via un export cURL DevTools de [prénom] — notre propre capture
        mitmproxy redacte Authorization/Cookie donc on n'avait jamais pu le voir).
        Sans ce header, le backend renvoie 500 au lieu de 200 : il tente
        vraisemblablement un chemin d'auth basé sur une session/cookie (absente ici,
        puisqu'on n'authentifie qu'avec le Bearer token) et plante dessus plutôt que
        de retomber proprement sur le token. Voir cartographie-edumoov.md §6.1.
        """
        headers = await self._headers()
        headers["X-Edumoov-Nosession"] = "true"
        headers["Accept"] = "application/json"
        return headers

    # ------------------------------------------------------------------
    # Couche RPC — api.edumoov.com/rpc/<domaine>.<ressource>.<action>
    # ------------------------------------------------------------------
    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Appelle une méthode RPC.

        Constat empirique (15/09/2026) : le framework RPC attend une enveloppe
        {"params": {...}, "payload": {}} et non un corps plat — confirmé via un
        export DevTools d'un appel navigateur réussi de `classroom.events.fetch`
        (qui échouait en HTTP 412 Precondition Failed avec un corps plat). Les
        endpoints qui toléraient jusqu'ici un corps plat (user.classrooms.fetch,
        etc.) le font probablement parce qu'ils ignorent leurs paramètres de
        toute façon (voir list_classrooms) — l'enveloppe complète est donc la
        forme sûre à utiliser partout désormais.
        """
        _check_allowed(method)
        url = f"{SETTINGS.rpc_base}/{method}"
        body = {"params": params or {}, "payload": {}}
        resp = await self._http.post(url, json=body, headers=await self._headers())
        if resp.status_code != 200:
            raise EdumoovApiError(f"RPC {method} : HTTP {resp.status_code}")
        payload = resp.json()
        if not payload.get("success", False):
            raise EdumoovApiError(f"RPC {method} a répondu success=false : {payload}")
        return payload.get("data")

    # ------------------------------------------------------------------
    # Couche REST classique — www.edumoov.com/api/1.0/...
    # ------------------------------------------------------------------
    async def rest_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        _check_allowed(path)
        url = f"{SETTINGS.rest_base}/{path.lstrip('/')}"
        resp = await self._http.get(url, params=_clean_params(params), headers=await self._rest_headers())
        if resp.status_code != 200:
            raise EdumoovApiError(f"GET {path} : HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            # Cf. cartographie §6.1 : la capture mitmproxy n'a jamais réussi à décoder ces
            # réponses (marquées "binary"). Un appel réel via httpx (gestion gzip/br
            # automatique) devrait normalement fonctionner ; si cette erreur survient
            # malgré tout, le problème est réel et pas un artefact de capture.
            raise EdumoovApiError(
                f"GET {path} : réponse HTTP 200 mais non décodable en JSON "
                f"(content-type={resp.headers.get('content-type')!r}). "
                "Voir cartographie-edumoov.md §6.1 (limitation connue, non résolue)."
            ) from exc

    # ------------------------------------------------------------------
    # Méthodes métier utilisées par les outils MCP (server.py)
    # ------------------------------------------------------------------
    async def list_classrooms(self, *, graph: list[str] | None = None) -> Any:
        """Toutes les classes visibles par l'utilisateur authentifié.

        Constat empirique (15/09/2026, testé en conditions réelles par [prénom]) :
        `user.classrooms.fetch` IGNORE tout paramètre de FILTRAGE envoyé — il
        renvoie systématiquement la liste complète des classes accessibles au
        compte (10 classes pour un compte direction sur l'école 11777, alors
        qu'un seul classroom_id avait été demandé). Documenté aussi dans
        cartographie-edumoov.md §6.2.

        En revanche `graph` fonctionne bien : il charge des données liées en une
        fois (ex. ["users"] pour les enseignants de chaque classe, avec
        id/nom/prénom/rôle/avatar — pas de mail dans cette source). Confirmé via
        export DevTools d'un appel navigateur réussi :
        {"limit":50,"graph":["school","grades","users","cartableSubscription"],"page":1}.
        """
        params = {"graph": graph} if graph else {}
        return await self.rpc("user.classrooms.fetch", params)

    async def get_classroom(self, classroom_id: str) -> Any:
        """Fiche d'une classe précise, filtrée CÔTÉ CLIENT (voir list_classrooms).

        Lève EdumoovApiError si l'id demandé n'apparaît pas dans la liste renvoyée
        par l'API (classe inexistante, ou inaccessible au compte authentifié).
        """
        classrooms = await self.list_classrooms()
        classroom_id_int = _coerce_id(classroom_id)
        for classroom in classrooms or []:
            if _coerce_id(classroom.get("id")) == classroom_id_int:
                return classroom
        raise EdumoovApiError(
            f"Classe {classroom_id!r} introuvable parmi les classes visibles par ce compte "
            f"({len(classrooms or [])} classe(s) renvoyée(s) par l'API)."
        )

    async def list_school_classrooms(self, school_id: str) -> Any:
        """Idem : `school_id` n'a pas été vérifié comme filtrant réellement côté
        serveur — à confirmer. En attendant, ne PAS supposer que ce filtre marche
        mieux que celui de list_classrooms()."""
        return await self.rpc("school.classrooms.fetch", {"school_id": school_id})

    async def get_school(self, school_id: str) -> Any:
        """Fiche établissement (adresse, UAI, directeur...). Filtrage serveur par
        `school_id` non vérifié (voir list_school_classrooms) — à confirmer au
        premier appel réel."""
        return await self.rpc("school.schools.fetch", {"school_id": school_id})

    async def list_school_teachers(self, school_id: str) -> list[dict[str, Any]]:
        """Annuaire enseignants d'une école, agrégé depuis les classes.

        Constat empirique (15/09/2026) : l'endpoint RPC dédié
        `school.schools.teachers` reste bloqué en HTTP 412 malgré une vingtaine
        de formes de paramètres essayées (voir cartographie-edumoov.md §6.2/§9)
        — abandonné pour l'instant, pas de header ou de forme de corps trouvée
        qui le débloque. Solution de repli robuste et déjà fonctionnelle :
        `user.classrooms.fetch` avec `graph=["users"]` inclut déjà les
        enseignants de chaque classe (id, nom, prénom, rôle TIT/ATSEM/etc.,
        avatar) ; on agrège et déduplique ici par école, filtrée CÔTÉ CLIENT
        comme les autres endpoints qui ignorent leurs filtres serveur (voir
        list_classrooms). Pas de champ mail dans cette source, contrairement à
        ce qu'on supposait avant de tester — à corriger si l'endpoint dédié
        finit par fonctionner.
        """
        classrooms = await self.list_classrooms(graph=["users"])
        teachers: dict[Any, dict[str, Any]] = {}
        for classroom in classrooms:
            if _coerce_id(classroom.get("school_id")) != _coerce_id(school_id):
                continue
            for user in classroom.get("users") or []:
                teachers[user.get("id")] = user
        return list(teachers.values())

    async def list_grades(self) -> Any:
        """Référentiel national des niveaux scolaires (TPS→CM2). Pas de paramètre :
        c'est un référentiel global, pas une donnée liée à un compte."""
        return await self.rpc("core.grades.fetch", {})

    async def list_classroom_events(
        self,
        classroom_id: str,
        *,
        start: str | None = None,
        stop: str | None = None,
        query: list[str] | None = None,
        graph: list[str] | None = None,
        page: int = 1,
    ) -> Any:
        """Événements/créneaux d'une classe.

        Constat empirique (15/09/2026) : contrairement aux endpoints RPC déjà
        vérifiés, celui-ci EXIGE plusieurs paramètres (pas seulement
        classroom_id) — confirmé via export DevTools d'un appel navigateur
        réussi : {"params": {"start":..., "stop":..., "query": ["where:status:=:BOOKED"],
        "graph": ["registrations"], "page": 1, "classroom_id": ...}, "payload": {}}.
        Sans eux (et sans l'enveloppe params/payload, voir rpc()), l'API
        renvoyait HTTP 412 Precondition Failed. Par défaut ici : fenêtre de 30
        jours à partir d'aujourd'hui, mêmes filtre/graph que l'appel réel
        observé — tous surchargeables si besoin.
        """
        today = datetime.date.today()
        params = {
            "classroom_id": classroom_id,
            "start": start or today.isoformat(),
            "stop": stop or (today + datetime.timedelta(days=30)).isoformat(),
            "query": query if query is not None else ["where:status:=:BOOKED"],
            "graph": graph if graph is not None else ["registrations"],
            "page": page,
        }
        return await self.rpc("classroom.events.fetch", params)

    async def get_user_settings(self, *, user_id: str | None = None, app: str = "educartable") -> Any:
        """Préférences de l'utilisateur authentifié (notifications, UI, favoris,
        vue par défaut).

        Constat empirique (15/09/2026) : contrairement à user.classrooms.fetch,
        cet endpoint EXIGE explicitement `id` (l'id utilisateur, pas déduit du
        token) et `app` (quelle application — "educartable" observé dans un
        appel réel). Sans ces deux paramètres, HTTP 412 Precondition Failed.
        Confirmé via export DevTools d'un appel navigateur réussi :
        {"params": {"id": 284395, "app": "educartable"}, "payload": {}}.
        `user_id` retombe sur EDUMOOV_DEFAULT_USER_ID si non fourni."""
        resolved_user_id = user_id or SETTINGS.default_user_id
        if not resolved_user_id:
            raise EdumoovApiError(
                "get_user_settings nécessite un identifiant utilisateur (paramètre "
                "user_id, ou variable d'environnement EDUMOOV_DEFAULT_USER_ID)"
            )
        try:
            resolved_user_id = int(resolved_user_id)
        except (TypeError, ValueError):
            pass
        return await self.rpc("user.settings.get", {"id": resolved_user_id, "app": app})

    async def list_pupils(self, classroom_id: str) -> list[dict[str, Any]]:
        """Liste des élèves d'une classe, DÉPAQUETÉE de l'enveloppe REST.

        Constat empirique (15/09/2026, testé en conditions réelles) : contrairement
        à ce qu'on supposait, cet endpoint REST renvoie lui aussi une enveloppe
        {success, data: [...], pagination: {...}} — pas une liste brute. Schéma réel
        d'un élève (confirmé, à ne plus deviner) : id, name, firstname, birthday
        (format JJ/MM/AAAA), gender, grade_id, classroom_id, ine, fullname,
        grade{id,letters,name}, classroom{...}, parents[{id,name,firstname,mail,
        PupilsUser{...}}], groups.

        IMPORTANT : c'est ICI, au niveau du client, que l'enveloppe est dépaquetée —
        pas dans server.py. Le premier appel réel a révélé que server.py ne
        redactait rien du tout tant que list_pupils() renvoyait l'enveloppe brute
        (isinstance(..., list) était toujours False). Dépaqueter ici garantit que
        tout appelant reçoit une vraie liste, redaction ou pas.
        """
        envelope = await self.rest_get(
            f"core/classroom/{classroom_id}/pupils", {"classroom_id": classroom_id}
        )
        return _unwrap_rest_envelope(envelope, context="GET .../pupils")

    async def list_cartable_items(
        self,
        classroom_id: str,
        *,
        types: Iterable[str] | None = None,
        box: str | None = None,
        archived: bool | None = None,
        pupil_id: str | None = None,
        achieved: Iterable[str] | None = None,
        start: str | None = None,
        stop: str | None = None,
        meetings_start: str | None = None,
        meetings_stop: str | None = None,
        sort: str | None = None,
        direction: str | None = None,
        page: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Endpoint unifié du cartable — cf. cartographie §4 et §6.1.

        `types` couvre : activity, lesson, event, alert, info, meeting, advert,
        code, image, text. `archived=True` -> Archives, `pupil_id`+`achieved` ->
        Suivi des élèves, `type=meeting`+`meetings_start/stop` -> RDV Parents.

        DÉPAQUETÉ de l'enveloppe REST (voir _unwrap_rest_envelope) — confirmé
        empiriquement le 15/09/2026, même forme d'enveloppe que /pupils. Schéma
        réel d'un élément (confirmé) : id, type, title, subject, subject_id, body,
        date, created, modified, visible, archived, visibility, color, icon,
        user_id, user, recipients[], recipients_count, medias[], events[],
        events_count, meetings[], comments_count, likes_count, achievement,
        achievements_count, commentable, pupil_present, template, survey,
        form_answers, games, links, revisions, recurrence, metadata, scope_key,
        scope_model, last_interaction. `body` contient le contenu réel du message
        (texte du cahier de liaison/texte/vie) — sensible par nature, ne pas
        logger.
        """
        params: dict[str, Any] = {"classroom_id": classroom_id}
        if types:
            params["type"] = ",".join(types)
        if box:
            params["box"] = box
        if archived is not None:
            params["archived"] = 1 if archived else 0
        if pupil_id:
            params["pupil_id"] = pupil_id
        if achieved:
            params["achieved"] = ",".join(achieved)
        if start:
            params["start"] = start
        if stop:
            params["stop"] = stop
        if meetings_start:
            params["meetings_start"] = meetings_start
        if meetings_stop:
            params["meetings_stop"] = meetings_stop
        if sort:
            params["sort"] = sort
        if direction:
            params["direction"] = direction
        if page is not None:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit
        envelope = await self.rest_get(f"cartable/classroom/{classroom_id}/messages", params)
        return _unwrap_rest_envelope(envelope, context="GET .../cartable/.../messages")

    async def list_recipients(self, classroom_id: str) -> list[dict[str, Any]]:
        """Destinataires (parents/contacts) d'une classe. Forme d'enveloppe non
        encore vérifiée pour CET endpoint précis (voir _unwrap_rest_envelope) —
        suppose la même forme que pupils/messages jusqu'à preuve du contraire."""
        envelope = await self.rest_get(
            f"core/classroom/{classroom_id}/recipients", {"classroom_id": classroom_id}
        )
        return _unwrap_rest_envelope(envelope, context="GET .../recipients")

    async def list_notifications(
        self,
        user_id: str,
        *,
        app: str | None = None,
        scope: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Notifications d'un utilisateur. `app` observé en capture : 'Educartable'."""
        params: dict[str, Any] = {}
        if app:
            params["app"] = app
        if scope:
            params["scope"] = scope
        if limit is not None:
            params["limit"] = limit
        envelope = await self.rest_get(f"core/user/{user_id}/notifications", params)
        return _unwrap_rest_envelope(envelope, context="GET .../notifications")

    async def list_item_comments(self, classroom_id: str, key: str) -> list[dict[str, Any]]:
        """Fil de commentaires sur un élément du cartable. `key` = l'`id` (ou uuid)
        d'un élément renvoyé par list_cartable_items."""
        envelope = await self.rest_get(
            f"core/classroom/{classroom_id}/comments",
            {"model": "message", "key": key, "thread": 1, "classroom_id": classroom_id},
        )
        return _unwrap_rest_envelope(envelope, context="GET .../comments")

    async def search_preps_sequences(self, user_id: str, query: str | None = None) -> list[dict[str, Any]]:
        """Recherche de séquences pédagogiques (bibliothèque partagée, voir
        cartographie §2). Enveloppe {success, data, pagination} confirmée
        (15/09/2026) — 6e endpoint REST sur 6 testés à suivre cette forme."""
        params: dict[str, Any] = {}
        if query:
            params["q"] = query
        envelope = await self.rest_get(f"edupreps/user/{user_id}/sequences/search", params)
        return _unwrap_rest_envelope(envelope, context="GET .../sequences/search")

    async def list_web2print_books(self, classroom_id: str) -> list[dict[str, Any]]:
        """Livres/exports photo (cahier de vie). Même enveloppe confirmée."""
        envelope = await self.rest_get(f"web2print/classroom/{classroom_id}/books")
        return _unwrap_rest_envelope(envelope, context="GET .../web2print/.../books")

    # ------------------------------------------------------------------
    # Livret (edulivret) — découvert le 15/09/2026 via une 4e capture réseau
    # ------------------------------------------------------------------
    # Constat majeur : les données Livret (évaluations, suivi de compétences,
    # réussites maternelle, ceintures, statistiques...) ne transitent quasiment
    # jamais par de simples requêtes HTTP observables dans l'onglet Network du
    # navigateur — elles passent par le canal **Socket.IO** temps réel déjà
    # identifié (`api.edumoov.com/socket.io/`), sous forme de JSON-RPC embarqué
    # dans les frames WebSocket : chaque appel est un message
    # `[méthode, params, {"$tz": ..., "$token": <access_token>}]`, la réponse
    # un ACK `[{success, data, paging?}]` — mêmes noms de méthodes
    # (`<domaine>.<ressource>.<action>`) que la couche RPC déjà utilisée
    # ailleurs dans ce client, mais le token voyage DANS le message plutôt que
    # dans un header HTTP. Capturé en ajoutant un hook `websocket_message` à
    # l'addon mitmproxy (`scripts/capture_addon.py`), les hooks HTTP classiques
    # ne voyant jamais ces frames.
    #
    # Bonne nouvelle vérifiée ensuite en conditions réelles : ces méthodes
    # répondent TOUTES très bien via notre endpoint HTTP `/rpc/` habituel
    # (POST + enveloppe params/payload, voir rpc()) — pas besoin d'implémenter
    # un client Socket.IO pour les utiliser depuis le connecteur.
    #
    # Point de vigilance découvert au passage : `classroom.pupils.fetch` (la
    # variante RPC de la liste d'élèves, différente de la REST déjà utilisée
    # par list_pupils()) inclut un champ `password` dans son schéma — vérifié
    # `None` sur les 22 élèves de la classe testée, mais le nom du champ à lui
    # seul justifie de ne JAMAIS exposer cette méthode brute dans un outil MCP
    # sans redaction explicite si elle est utilisée un jour (voir aussi le
    # garde-fou existant sur `pupils/codes` en REST). Non utilisée ici : les
    # méthodes ci-dessous s'en tiennent à évaluations/suivi/réussites/ceintures.

    async def list_evaluations(
        self,
        classroom_id: str,
        *,
        query: list[str] | None = None,
        graph: list[str] | None = None,
        order_by: str = "date:desc",
        page: int = 1,
        limit: int = 100,
    ) -> Any:
        """Évaluations du Livret (notes/résultats par matière et par période).

        `query` suit la même syntaxe que le reste de l'API :
        `["where:date:>=:YYYY-MM-DD", "where:date:<:YYYY-MM-DD"]`, observée
        dans un appel réel avec en plus `whereNull:type` et
        `where:success:=:false` pour ne garder que les évaluations en échec
        sans type particulier — libre au niveau du filtre, non ré-abstrait ici.
        Sans `query`, l'API renvoie ses résultats par défaut (comportement de
        filtrage non garanti, voir la remarque générale sur les paramètres RPC
        en cartographie §6.2)."""
        params: dict[str, Any] = {
            "classroom_id": classroom_id,
            "orderBy": order_by,
            "page": page,
            "limit": limit,
        }
        if query is not None:
            params["query"] = query
        if graph is not None:
            params["graph"] = graph
        return await self.rpc("classroom.evaluations.fetch", params)

    async def list_assessments(
        self,
        classroom_id: str,
        *,
        start: str | None = None,
        stop: str | None = None,
        query: list[str] | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> Any:
        """Suivi de compétences par élève (section « Suivi des élèves » du
        Livret) — enregistrements ponctuels d'acquisition, pas les évaluations
        notées (voir list_evaluations). `query` permet par ex. de filtrer sur
        un élève précis (`["where:pupil_id:=:<id>"]`, observé dans un appel
        réel). Fenêtre par défaut : 1 an en arrière à 30 jours en avant, comme
        pour list_classroom_events — à ajuster selon les périodes réelles de
        l'établissement si besoin de précision."""
        today = datetime.date.today()
        params: dict[str, Any] = {
            "classroom_id": classroom_id,
            "start": start or (today - datetime.timedelta(days=365)).isoformat(),
            "stop": stop or (today + datetime.timedelta(days=30)).isoformat(),
            "page": page,
            "limit": limit,
        }
        if query is not None:
            params["query"] = query
        return await self.rpc("classroom.assessments.fetch", params)

    async def list_mater_skills(
        self,
        classroom_id: str,
        *,
        query: list[str] | None = None,
        graph: list[str] | None = None,
        order_by: str = "date:desc",
        page: int = 1,
        limit: int = 100,
    ) -> Any:
        """Réussites maternelle (section dédiée du Livret pour les classes de
        maternelle — badges/réussites par domaine, distincts des évaluations
        classiques). Même style de paramètres que list_evaluations ;
        `graph=["skillsItems"]` observé dans un appel réel pour charger le
        détail des compétences associées."""
        params: dict[str, Any] = {
            "classroom_id": classroom_id,
            "orderBy": order_by,
            "page": page,
            "limit": limit,
        }
        if query is not None:
            params["query"] = query
        if graph is not None:
            params["graph"] = graph
        return await self.rpc("classroom.mater_skills.fetch", params)

    async def list_belts(
        self,
        classroom_id: str,
        *,
        page: int = 1,
        limit: int = 500,
        graph: list[str] | None = None,
    ) -> Any:
        """Ceintures de compétences activées pour la classe (système de
        progression par « ceintures », désactivé par défaut — voir
        get_classroom_settings pour savoir si `LivretBelts` est actif avant
        d'appeler ceci). Vide si aucune ceinture n'a été activée par
        l'enseignant, ce qui n'est pas une erreur. `graph=["beltLevel"]`
        observé dans un appel réel."""
        params: dict[str, Any] = {"classroom_id": classroom_id, "page": page, "limit": limit}
        if graph is not None:
            params["graph"] = graph
        return await self.rpc("classroom.belts.fetch", params)

    async def get_classroom_settings(
        self, classroom_id: str, *, app: str = "Livret", context: str = "all"
    ) -> Any:
        """Configuration Livret de la classe : fonctionnalités activées
        (`features`, ex. `LivretBelts`, `LivretAttestations`) et barème de
        notation configuré (`codes`) — utile pour interpréter le champ
        `notation`/`success` des évaluations renvoyées par list_evaluations.
        Paramètres confirmés via un appel réel :
        {"app": "Livret", "context": "all", "classroom_id": ...}."""
        return await self.rpc(
            "classroom.settings.get",
            {"app": app, "context": context, "classroom_id": classroom_id},
        )

    # ------------------------------------------------------------------
    # Médias — GET api.edumoov.com/rpc/core.medias.file (endpoint atypique,
    # cf. avertissement sécurité ci-dessous)
    # ------------------------------------------------------------------
    async def get_media_url(self, media_id: str, *, token: str | None = None) -> str:
        """Résout un id de média Edumoov en URL de téléchargement signée
        (temporaire, hébergée sur storage.gra.cloud.ovh.net).

        Détails d'implémentation confirmés empiriquement (15/09/2026) :
        - Ne JAMAIS envoyer de header `Authorization` sur cet endpoint précis :
          un Bearer seul, sans `token` dans l'URL, provoque un HTTP 403 — à
          l'inverse de tous les autres endpoints RPC. D'où l'appel HTTP direct
          ci-dessous plutôt qu'un passage par `rpc()`/`_headers()`.
        - Le `token` optionnel (récupéré, quand disponible, depuis un objet qui
          référence déjà ce média — ex. `classroom.settings.get` →
          `header.logoFile`/`signatures.signatureFile`) n'est PAS vérifié par le
          endpoint pour les médias testés : un `id` seul, sans token ni aucune
          authentification, a suffi à obtenir la même URL signée finale (id, bon
          token, mauvais token et absence de token ont tous renvoyé exactement le
          même HTTP 302 vers la même URL OVH, même `temp_url_sig`).

        ⚠️ Implication sécurité, volontairement PAS exploitée plus avant ici :
        si les `media_id` sont devinables/séquentiels (ce test n'a porté que sur
        des ids déjà légitimement accessibles au compte authentifié — aucune
        tentative d'énumération d'autres ids n'a été faite), n'importe qui
        connaissant un `media_id` pourrait obtenir l'URL de téléchargement d'un
        média sans être authentifié. Voir cartographie-edumoov.md §6.2 pour le
        détail et la recommandation de signalement à Edumoov — ce connecteur ne
        fait qu'exposer le comportement observé de l'API telle qu'elle est.
        """
        params: dict[str, Any] = {"id": media_id}
        if token:
            params["token"] = token
        resp = await self._http.get(
            f"{SETTINGS.rpc_base}/core.medias.file",
            params=params,
            follow_redirects=False,
        )
        location = resp.headers.get("location")
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            return location
        raise EdumoovApiError(
            f"edumoov_media_get_url : réponse inattendue (HTTP {resp.status_code}, "
            f"pas de redirection vers une URL signée)."
        )


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}
