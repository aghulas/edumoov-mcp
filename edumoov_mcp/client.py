"""Client HTTP vers les deux surfaces API d'Edumoov identifiées par capture réseau.

Voir cartographie-edumoov.md §6 pour le détail :
- RPC (api.edumoov.com/rpc/<domaine>.<ressource>.<action>) : données de structure
  (école, classe, enseignants, référentiels). Enveloppe {success, data, paging?}.
- REST classique (www.edumoov.com/api/1.0/...) : contenu du cartable (messages,
  devoirs, activités, RDV, suivi, archives — tous derrière le même endpoint
  /cartable/classroom/{id}/messages, filtré par query params).

Garde-fou de sécurité : `_FORBIDDEN_PATH_MARKERS` bloque au niveau du client, et pas
seulement par omission côté outils MCP, tout endpoint qui exposerait des codes d'accès
(voir spec-connecteur-mcp-edumoov.md §3). Seule exception, depuis le 07/10/2026 :
`fetch_family_codes`, chemin dédié vers les codes familles, réservé à l'outil local
qui produit les fiches PDF à remettre aux parents (family_codes.py).
"""
from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Iterable

import httpx

from .auth import KeycloakAuth
from .config import SETTINGS

# Jamais interrogé, quel que soit l'appelant : retourne les codes d'accès individuels
# des élèves (équivalent à un mot de passe de mineur). Voir cartographie §6.1 et
# spec §3 — aucun outil MCP ne doit jamais atteindre cet endpoint.
_FORBIDDEN_PATH_MARKERS: tuple[str, ...] = ("pupils/codes",)


class _RedactSignedUrls(logging.Filter):
    """httpx journalise chaque requête avec son URL complète (niveau INFO) : on masque
    la signature des URL de fichiers Edumoov (`temp_url_sig`, valable 12 h sans
    authentification) pour qu'elle n'atterrisse jamais dans les journaux du client MCP."""

    _RX = re.compile(r"\?[^\s\"']*temp_url_sig=[^\s\"']*")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                self._RX.sub("?<signature masquée>", str(a)) if "temp_url_sig" in str(a) else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        if isinstance(record.msg, str):
            record.msg = self._RX.sub("?<signature masquée>", record.msg)
        return True


logging.getLogger("httpx").addFilter(_RedactSignedUrls())


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
        15/09/2026, via un export cURL DevTools — notre propre capture
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

    async def _rpc_all_pages(
        self, method: str, params: dict[str, Any] | None = None, *, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """Comme `rpc()`, mais boucle sur toutes les pages si l'endpoint pagine.

        Incident du 17/09/2026 (voir cartographie-edumoov.md §9 et list_classrooms
        ci-dessous) : `user.classrooms.fetch` pagine bel et bien ses résultats, avec
        un défaut SILENCIEUX de `limit=10` si on ne précise rien — ce qui avait fait
        disparaître 5 classes sur 15 (et par ricochet leurs 5 enseignants de
        `list_school_teachers`) sans la moindre erreur, juste une liste incomplète
        renvoyée avec `success: true`. Diagnostiqué en comparant le compte réel de
        classes visible dans l'interface Edumoov (15) à celui renvoyé par le
        connecteur (10), puis confirmé en inspectant le champ `paging` de la réponse
        brute (`{"page":1,"limit":10,"pages":2,"total":15,"next":true,...}`), resté
        invisible jusqu'ici car `rpc()` ne renvoie que `payload["data"]`.

        Leçon générale qui motive cette méthode plutôt qu'un simple correctif ponctuel
        sur `list_classrooms` : les paramètres de FILTRAGE (`classroom_id`, etc.) sont
        bien ignorés par plusieurs endpoints RPC (voir list_classrooms), mais rien ne
        garantit qu'un endpoint RPC tienne en une seule page — ne plus jamais supposer
        qu'un appel RPC sans pagination explicite renvoie tout, sans avoir vérifié son
        champ `paging`.
        """
        _check_allowed(method)
        url = f"{SETTINGS.rpc_base}/{method}"
        all_rows: list[dict[str, Any]] = []
        page = 1
        while True:
            body_params = dict(params or {})
            body_params["page"] = page
            body_params["limit"] = page_size
            resp = await self._http.post(
                url, json={"params": body_params, "payload": {}}, headers=await self._headers()
            )
            if resp.status_code != 200:
                raise EdumoovApiError(f"RPC {method} (page {page}) : HTTP {resp.status_code}")
            payload = resp.json()
            if not payload.get("success", False):
                raise EdumoovApiError(
                    f"RPC {method} (page {page}) a répondu success=false : {payload}"
                )
            all_rows.extend(payload.get("data") or [])
            paging = payload.get("paging") or {}
            if not paging.get("next"):
                break
            page += 1
            if page > 50:
                # Garde-fou anti-boucle infinie si l'API renvoie un `paging`
                # incohérent (next toujours true) — mieux vaut échouer bruyamment
                # que boucler indéfiniment ou renvoyer une liste tronquée en silence.
                raise EdumoovApiError(
                    f"RPC {method} : pagination interrompue après 50 pages "
                    "(paging incohérent côté API ?)."
                )
        return all_rows

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

        Constat empirique (15/09/2026, testé en conditions réelles) :
        `user.classrooms.fetch` IGNORE tout paramètre de FILTRAGE envoyé (ex. un
        `classroom_id` précis) — il renvoie la liste des classes accessibles au
        compte plutôt qu'une classe unique. Documenté aussi dans
        cartographie-edumoov.md §6.2.

        **Correction importante (17/09/2026)** : l'affirmation initiale selon
        laquelle cet appel renvoyait « systématiquement la liste complète » était
        FAUSSE — l'endpoint pagine silencieusement (défaut `limit=10`), ce qui a
        fait disparaître 5 classes sur 15 de l'école (et leurs enseignants,
        propagé à `list_school_teachers`) sans aucune erreur. Voir
        `_rpc_all_pages` et cartographie-edumoov.md §9 pour le détail complet de
        l'incident et le correctif : cette méthode boucle désormais sur toutes
        les pages plutôt que de s'arrêter à la première.

        `graph` fonctionne bien : il charge des données liées en une fois (ex.
        ["users"] pour les enseignants de chaque classe, avec
        id/nom/prénom/rôle/avatar — pas de mail dans cette source). Confirmé via
        export DevTools d'un appel navigateur réussi :
        {"limit":50,"graph":["school","grades","users","cartableSubscription"],"page":1}
        — un appel qui, avec le recul, donnait déjà la bonne intuition (`limit`
        explicite) sans qu'on en tire la conséquence sur le comportement par
        défaut sans ce paramètre.
        """
        params = {"graph": graph} if graph else {}
        return await self._rpc_all_pages("user.classrooms.fetch", params)

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
        mieux que celui de list_classrooms(). Pas encore câblé à un outil MCP actif
        (voir server.py) au 17/09/2026.

        Pagine comme `user.classrooms.fetch` (même défaut silencieux `limit=10`,
        vérifié empiriquement le 17/09/2026 — voir `_rpc_all_pages` et
        cartographie-edumoov.md §9) — corrigé ici par précaution avant même d'être
        câblée à un outil, pour ne pas propager le même piège si elle l'est un jour.
        """
        return await self._rpc_all_pages("school.classrooms.fetch", {"school_id": school_id})

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
        c'est un référentiel global, pas une donnée liée à un compte.

        Pagine (confirmé le 17/09/2026, même défaut silencieux `limit=10` que
        `user.classrooms.fetch` — voir `_rpc_all_pages` et cartographie-edumoov.md
        §9) : le référentiel complet compte 18 entrées, pas 10 — un appel simple
        via `rpc()` en aurait tronqué le tiers sans la moindre erreur."""
        return await self._rpc_all_pages("core.grades.fetch", {})

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
        {"params": {"id": 12345, "app": "educartable"}, "payload": {}}.
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
    # Journal (cahier journal / emploi du temps) — POST api.edumoov.com/rpc/
    # user.clog_*.fetch et classroom.clog_slots.fetch. Découvert le 15/09/2026
    # via le canal WebSocket propre à l'app Journal (console navigateur,
    # préfixe [apiQueryBuilder]), confirmé équivalent en HTTP RPC le
    # 16/09/2026 en testant systématiquement le pattern <domaine>.<ressource>.<action>
    # déjà identifié en §6.2/§6.3 de cartographie-edumoov.md — contrairement à
    # Livret, le domaine correct ici est `user` (et non `classroom`) pour la
    # plupart des ressources, cohérent avec l'URL /journal/user/{userId}.
    # Schémas non confirmés sur données réelles : le compte de test n'a aucune
    # séance/créneau saisi en ce début d'année scolaire (réponses vides mais
    # HTTP 200 — enveloppe generique confirmée, contenu non caractérisé).
    # Voir cartographie §6.6 pour le détail de la démarche, dont le cas non
    # résolu de l'app Appel (aucun nom de méthode RPC trouvé, ~15 essais).
    # ------------------------------------------------------------------
    async def list_journal_lessons(
        self, user_id: str, *, page: int = 1, limit: int = 200
    ) -> Any:
        """Séances du cahier journal (cours/activités planifiés) d'un
        enseignant. Vide sur le compte de test — schéma non confirmé au-delà
        de l'enveloppe générique {success, data, paging?}."""
        params: dict[str, Any] = {"user_id": user_id, "page": page, "limit": limit}
        return await self.rpc("user.clog_lessons.fetch", params)

    async def list_journal_schedules(self, user_id: str) -> Any:
        """Emploi du temps (créneaux hebdomadaires récurrents) d'un
        enseignant. Schéma non confirmé (vide sur le compte de test).

        Pagine via `_rpc_all_pages` par précaution (17/09/2026) : aucun paramètre
        page/limit n'est exposé à l'appelant MCP ici, exactement le schéma qui a
        fait disparaître 5 classes sur 15 pour `list_classrooms` avant correctif
        (voir cartographie-edumoov.md §9) — vide aujourd'hui sur le compte de
        test, mais un emploi du temps hebdomadaire réel peut dépasser 10
        créneaux sans peine une fois l'année scolaire avancée."""
        return await self._rpc_all_pages("user.clog_schedules.fetch", {"user_id": user_id})

    async def list_journal_slots(self, classroom_id: str) -> Any:
        """Créneaux horaires du cahier journal d'une classe (récréations,
        pause méridienne, etc., visibles dans la grille hebdomadaire).
        Schéma non confirmé (vide sur le compte de test).

        Pagine via `_rpc_all_pages` par précaution (17/09/2026) — même raisonnement
        que `list_journal_schedules` ci-dessus."""
        return await self._rpc_all_pages(
            "classroom.clog_slots.fetch", {"classroom_id": classroom_id}
        )

    async def list_journal_pedagroups(self, user_id: str) -> Any:
        """Groupes pédagogiques (sous-groupes d'élèves pour la
        différenciation) rattachés au cahier journal d'un enseignant. Schéma
        non confirmé (vide sur le compte de test).

        Pagine via `_rpc_all_pages` par précaution (17/09/2026) — même raisonnement
        que `list_journal_schedules` ci-dessus."""
        return await self._rpc_all_pages("user.clog_pedagroups.fetch", {"user_id": user_id})

    # ------------------------------------------------------------------
    # Médias — GET api.edumoov.com/rpc/core.medias.file (endpoint atypique,
    # cf. avertissement sécurité ci-dessous)
    # ------------------------------------------------------------------
    async def get_media_url(self, media_id: str, *, token: str | None = None) -> str:
        """Résout un id de média Edumoov en URL de téléchargement signée
        (temporaire, hébergée sur storage.gra.cloud.ovh.net ou filerz.edumoov.com).

        Comportement revérifié le 01/10/2026, après le correctif Edumoov du
        16/09/2026 (ticket n°51164, cartographie-edumoov.md §6.9) :
        - médias à id NUMÉRIQUE (logo, signature...) : `token` désormais
          obligatoire et vérifié — sans token ou avec un mauvais token, HTTP 404
          (volontairement identique à un id inexistant). Le token est fourni par
          l'objet qui référence le média (ex. `classroom.settings.get` →
          `header.logoFile` = `...core.medias.file?id=<id>&token=<token>`).
        - médias à id UUID (photos du cahier de vie) : toujours accessibles avec
          le seul id, sans token ni authentification (HTTP 302) — le correctif ne
          les couvre pas. Constat signalé dans la doc projet, à remonter à
          Edumoov ; ne pas exploiter au-delà de l'usage légitime.
        - Ne JAMAIS envoyer de header `Authorization` sur cet endpoint : un Bearer
          seul provoque un HTTP 403 (constat du 15/09/2026, inverse de tous les
          autres endpoints RPC). D'où l'appel HTTP direct ci-dessous.
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
        if resp.status_code == 404 and not token:
            raise EdumoovApiError(
                "edumoov_media_get_url : HTTP 404 — pour un média à id numérique, le "
                "paramètre `token` est obligatoire depuis le correctif Edumoov du "
                "16/09/2026 (le récupérer dans l'URL qui référence le média, ex. "
                "header.logoFile de edumoov_classroom_settings_get)."
            )
        raise EdumoovApiError(
            f"edumoov_media_get_url : réponse inattendue (HTTP {resp.status_code}, "
            f"pas de redirection vers une URL signée)."
        )

    # ------------------------------------------------------------------
    # Appel et licences (01/10/2026) — méthodes lues dans le bundle front
    # app.edumoov.com (entité `appeals`, endpointName `pupilsappeals` ; entité
    # `subscriptions`) puis validées en lecture réelle. Voir cartographie §6.11.
    # ------------------------------------------------------------------
    async def list_appeals(
        self,
        school_id: str,
        *,
        start: str,
        stop: str,
        classroom_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lignes d'appel (une par élève et par demi-journée) d'une école sur une
        période. Champs : pupil_id, classroom_id, date, pm, present, delay,
        arrival, departure, eat/ate, study/studied, play/played, unjustified,
        ignore, comment."""
        query = [f"where:date:>=:{start}", f"andWhere:date:<=:{stop}"]
        if classroom_id:
            query.append(f"andWhere:classroom_id:=:{int(classroom_id)}")
        return await self._rpc_all_pages(
            "school.pupilsappeals.fetch",
            {"school_id": int(school_id), "query": query},
            page_size=500,
        )

    async def appeal_stats(
        self,
        school_id: str,
        *,
        date: str,
        period: str = "day",
        classroom_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Synthèse d'appel par classe et demi-journée (présents, absents,
        cantine, étude, garderie, retards). `period` : day | month (`date` =
        premier jour du mois pour month)."""
        params: dict[str, Any] = {"school_id": int(school_id), "date": date, "period": period, "limit": 62}
        if classroom_id:
            params["classroom_id"] = int(classroom_id)
        return await self.rpc("school.pupilsappeals.stats", params) or []

    async def start_registers_job(
        self,
        school_id: str,
        *,
        types: list[str],
        months: list[str],
        classroom_ids: list[int],
        color: bool = False,
    ) -> dict[str, Any]:
        """Lance la génération des registres d'appel (Direction → Appel → Registres,
        bouton « Télécharger pour toutes les classes »). Méthode lue dans le bundle
        front (`Appeal.apiDownloadRegisters`, menu EDirectionRegistersDownloadMenu) et
        validée en réel le 04/10/2026 : réponse HTTP 202 `{success, job}` (job
        orchestrateur `appeal.downloadRegisters`), résultat = une archive zip avec un
        PDF par type et par mois (appel/, cantine/, etude/, periscolaire/).
        N'écrit aucune donnée d'appel."""
        method = "school.pupilsappeals.downloadRegisters"
        _check_allowed(method)
        resp = await self._http.post(
            f"{SETTINGS.rpc_base}/{method}",
            json={
                "params": {"school_id": int(school_id)},
                "payload": {
                    "types": types,
                    "months": months,
                    "classroomIds": [int(c) for c in classroom_ids],
                    "color": bool(color),
                },
            },
            headers=await self._headers(),
        )
        body = resp.json() if resp.content else None
        if resp.status_code not in (200, 202) or not isinstance(body, dict) or not body.get("success"):
            error = body.get("error") if isinstance(body, dict) else None
            raise EdumoovApiError(f"RPC {method} : HTTP {resp.status_code}, erreur={error}")
        job = body.get("job") or body.get("data")
        if not isinstance(job, dict) or not job.get("id"):
            raise EdumoovApiError(f"RPC {method} : aucun job dans la réponse.")
        return job

    _PDF_PATH = re.compile(r"^/appeals/\d{4}-\d{2}-\d{2}/(color|grey|empty)$")

    async def start_pdf_job(
        self, scope_model: str, scope_id: str, *, app: str, path: str, filename: str
    ) -> dict[str, Any]:
        """Lance la génération serveur d'un PDF d'une page de l'app (job
        orchestrateur `pdf.temporary`, `Job.apiTempPdf` du front) :
        `<scope>.jobs.tempPdf`, params `{app, <scope>_id}`, payload `{path,
        filename}`. Utilisé par Direction → Appel pour « Appel du jour en couleur /
        noir et blanc » et « Appel vierge non daté » (path
        `/appeals/<AAAA-MM-JJ>/<color|grey|empty>`), validé en réel le 04/10/2026
        pour une classe et pour l'école. Seuls ces chemins sont autorisés ici."""
        if scope_model not in ("school", "classroom"):
            raise ForbiddenEndpointError(f"Portée de PDF non autorisée : {scope_model!r}")
        if not self._PDF_PATH.match(path):
            raise ForbiddenEndpointError(f"Chemin de PDF non autorisé : {path!r}")
        method = f"{scope_model}.jobs.tempPdf"
        resp = await self._http.post(
            f"{SETTINGS.rpc_base}/{method}",
            json={
                "params": {"app": app, f"{scope_model}_id": int(scope_id)},
                "payload": {"path": path, "filename": filename},
            },
            headers=await self._headers(),
        )
        body = resp.json() if resp.content else None
        if resp.status_code not in (200, 202) or not isinstance(body, dict) or not body.get("success"):
            error = body.get("error") if isinstance(body, dict) else None
            raise EdumoovApiError(f"RPC {method} : HTTP {resp.status_code}, erreur={error}")
        job = body.get("data") or body.get("job")
        if not isinstance(job, dict) or not job.get("id"):
            raise EdumoovApiError(f"RPC {method} : aucun job dans la réponse.")
        return job

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """État d'un job orchestrateur de l'utilisateur (`user.jobs.get`, comme le
        front pour les PDF et les registres). Statuts : 3-6 en cours, 7 terminé,
        1/2/8/9/10/12/13 en erreur (`Job.statusMap` du front)."""
        return await self.rpc("user.jobs.get", {"id": job_id}) or {}

    async def fetch_job_file(self, url: str) -> tuple[bytes, str | None]:
        """Télécharge le fichier produit par un job (URL signée `filerz.edumoov.com`,
        valable 12 h, sans authentification). Hôte vérifié : rien d'autre n'est
        téléchargé."""
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not (host == "edumoov.com" or host.endswith(".edumoov.com")):
            raise ForbiddenEndpointError(f"Téléchargement refusé : hôte inattendu {host!r}.")
        resp = await self._http.get(url, follow_redirects=True, timeout=120.0)
        if resp.status_code != 200:
            raise EdumoovApiError(f"Téléchargement du fichier : HTTP {resp.status_code}")
        return resp.content, resp.headers.get("content-disposition")

    async def list_subscriptions(self, school_id: str) -> list[dict[str, Any]]:
        """Licences de l'école (classes et enseignants), toutes années
        confondues, avec la facture associée. Pagine (274 lignes au 01/10/2026)."""
        return await self._rpc_all_pages(
            "school.subscriptions.fetch", {"school_id": int(school_id)}, page_size=50
        )

    # ------------------------------------------------------------------
    # Écriture (30/09/2026) — voir writes.py (garde-fou prévisualiser/confirmer)
    # et cartographie-edumoov.md §6.11. Règle de nommage RPC lue dans le bundle
    # front (`NG.rpc`) : `<scope.model>.<entité>.<action>`, et l'id du scope est
    # injecté en paramètre `<scope.model>_id`. Ex. une annonce d'école =
    # entité `messages` (type "advert") dans le scope `school` →
    # `school.messages.create` avec `school_id`.
    # ------------------------------------------------------------------
    async def rpc_write(
        self, method: str, params: dict[str, Any], payload: dict[str, Any]
    ) -> Any:
        """Envoie un appel RPC d'écriture. N'est appelé QUE par
        edumoov_write_confirm, avec un appel préalablement prévisualisé et
        présent dans la liste blanche de writes.py (revérifiée ici)."""
        from .writes import ALLOWED_WRITE_METHODS

        _check_allowed(method)
        if method not in ALLOWED_WRITE_METHODS:
            raise ForbiddenEndpointError(f"Méthode d'écriture non autorisée : {method!r}")
        url = f"{SETTINGS.rpc_base}/{method}"
        resp = await self._http.post(
            url, json={"params": params, "payload": payload}, headers=await self._headers()
        )
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code != 200 or not isinstance(body, dict) or not body.get("success"):
            error = body.get("error") if isinstance(body, dict) else None
            raise EdumoovApiError(f"RPC {method} : HTTP {resp.status_code}, erreur={error}")
        return body.get("data")

    async def upload_media(
        self, method: str, params: dict[str, Any], payload: dict[str, Any], file_path: str
    ) -> dict[str, Any]:
        """Envoie un fichier comme média Edumoov (pièce jointe d'une annonce).
        N'est appelé QUE par edumoov_write_confirm (transport "upload").

        1. RPC `school.medias.url` {params: {school_id, method:"POST", lts:false},
           payload: {model, key, links}} → {url, sig, payload, expires, iat} ;
        2. POST multipart vers `url` (hôte *.edumoov.com) : champs `sig`,
           `payload`, puis `file` → JSON du média créé {id, name, size, ...}.
        Le lien primaire (model/key) rattache le média à l'objet côté serveur.
        L'URL signée n'est ni renvoyée ni journalisée."""
        from pathlib import Path
        from urllib.parse import urlparse

        from .writes import ALLOWED_UPLOAD_METHODS

        _check_allowed(method)
        if method not in ALLOWED_UPLOAD_METHODS:
            raise ForbiddenEndpointError(f"Envoi de fichier non autorisé : {method!r}")
        extra_fields: dict[str, str] = {}
        if method == "core.classroom.medias.url":
            # Cahier de liaison (couche REST legacy) : le lien primaire est
            # passé en champs du formulaire multipart, pas dans la signature.
            classroom_id = int(params["classroom_id"])
            body = await self.rest_get(f"core/classroom/{classroom_id}/medias/url")
            if not isinstance(body, dict) or not body.get("success"):
                raise EdumoovApiError("GET core/classroom/{id}/medias/url : réponse inattendue")
            extra_fields = {"model": str(payload["model"]), "key": str(payload["key"])}
        else:
            sign_params = {**params, "method": "POST", "lts": False}
            resp = await self._http.post(
                f"{SETTINGS.rpc_base}/{method}",
                json={"params": sign_params, "payload": payload},
                headers=await self._headers(),
            )
            try:
                body = resp.json()
            except ValueError:
                body = None
            if resp.status_code != 200 or not isinstance(body, dict) or not body.get("success"):
                raise EdumoovApiError(f"RPC {method} : HTTP {resp.status_code}")
        data = body.get("data") or {}
        url = data.get("url") or ""
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not (host == "edumoov.com" or host.endswith(".edumoov.com")):
            raise EdumoovApiError("URL d'envoi inattendue (hôte hors edumoov.com) : envoi annulé.")
        p = Path(file_path)
        mime = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg"}.get(p.suffix.lower(), "application/octet-stream")
        up = await self._http.post(
            url,
            data={**extra_fields, "sig": data.get("sig", ""), "payload": data.get("payload", "")},
            files={"file": (p.name, p.read_bytes(), mime)},
            timeout=120.0,
        )
        if up.status_code not in (200, 201):
            raise EdumoovApiError(f"Envoi du fichier refusé (HTTP {up.status_code}).")
        try:
            media = up.json()
        except ValueError as exc:
            raise EdumoovApiError("Envoi du fichier : réponse non JSON.") from exc
        if not isinstance(media, dict) or not media.get("id"):
            raise EdumoovApiError("Envoi du fichier : réponse sans identifiant de média.")
        return {k: media.get(k) for k in ("id", "name", "size", "extension", "type") if k in media}

    async def get_cartable_message_medias(self, classroom_id: str, message_id: str) -> list[dict[str, Any]]:
        """Médias rattachés à un message du cahier de liaison (relecture)."""
        envelope = await self.rest_get(
            f"core/classroom/{int(classroom_id)}/medias", {"link": f"message.{message_id}"}
        )
        rows = envelope.get("data") if isinstance(envelope, dict) else envelope
        return [{k: m.get(k) for k in ("id", "name", "size", "extension")}
                for m in (rows or []) if isinstance(m, dict)]

    async def get_advert_medias(self, school_id: str, advert_id: str) -> list[dict[str, Any]]:
        """Médias rattachés à une annonce (relecture après envoi)."""
        data = await self.rpc(
            "school.messages.get",
            {"school_id": int(school_id), "id": advert_id, "graph": ["medias"]},
        )
        medias = (data or {}).get("medias") or []
        return [{k: m.get(k) for k in ("id", "name", "size", "extension")} for m in medias if isinstance(m, dict)]

    async def rest_write(
        self,
        http_method: str,
        path: str,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | None,
    ) -> Any:
        """Écriture sur la couche REST legacy (Cartable). Même logique que
        rpc_write : n'est appelé que par edumoov_write_confirm, liste blanche
        revérifiée ici (writes.ALLOWED_REST_WRITES)."""
        from .writes import rest_write_allowed

        _check_allowed(path)
        if not rest_write_allowed(http_method, path):
            raise ForbiddenEndpointError(f"Écriture REST non autorisée : {http_method} {path!r}")
        resp = await self._http.request(
            http_method,
            f"{SETTINGS.rest_base}/{path}",
            params=_clean_params(params),
            json=payload if http_method != "DELETE" else None,
            headers=await self._rest_headers(),
        )
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code not in (200, 201) or not isinstance(body, dict) or not body.get("success"):
            detail = body.get("data") if isinstance(body, dict) else None
            message = detail.get("message") if isinstance(detail, dict) else None
            raise EdumoovApiError(f"{http_method} {path} : HTTP {resp.status_code} {message or ''}".strip())
        return body.get("data")

    async def fetch_family_codes(self, classroom_id: str, pupil_ids: list[int]) -> list[dict[str, Any]]:
        """Codes familles (identifiant + mot de passe du portail familles) des élèves
        demandés : POST core/classroom/{id}/pupils/codes, payload = [id, …] — appel
        du bouton « Exporter » de la page Codes familles. SEUL chemin autorisé vers
        `pupils/codes` (le marqueur reste bloqué partout ailleurs) ; n'est appelé que
        par family_codes.edumoov_family_codes_pdf, qui ne renvoie jamais les codes.
        Réponse jamais journalisée."""
        path = f"core/classroom/{int(classroom_id)}/pupils/codes"
        resp = await self._http.post(
            f"{SETTINGS.rest_base}/{path}",
            json=[int(i) for i in pupil_ids],
            headers=await self._rest_headers(),
        )
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code not in (200, 201) or not isinstance(body, dict) or not body.get("success"):
            raise EdumoovApiError(f"Codes familles : HTTP {resp.status_code}")
        data = body.get("data")
        if not isinstance(data, list):
            raise EdumoovApiError("Codes familles : réponse dans un format inattendu.")
        return [d for d in data if isinstance(d, dict)]

    async def get_cartable_message(self, classroom_id: str, message_id: str) -> dict[str, Any]:
        """Un élément du cartable par son id (GET .../messages/{id})."""
        envelope = await self.rest_get(
            f"cartable/classroom/{classroom_id}/messages/{message_id}", {"classroom_id": classroom_id}
        )
        if isinstance(envelope, dict) and isinstance(envelope.get("data"), dict):
            return envelope["data"]
        raise EdumoovApiError("GET .../messages/{id} : réponse dans un format inattendu.")

    async def list_adverts(self, school_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Annonces d'école (brouillons, programmées et publiées), plus récentes
        d'abord. `school.messages.fetch` filtré sur type=advert — validé le
        30/09/2026. `visibility` null = brouillon, date future = programmée,
        date passée = publiée (règle reprise du front : ei.status)."""
        return await self.rpc(
            "school.messages.fetch",
            {
                "school_id": int(school_id),
                "graph": ["recipients"],
                "query": ["where:type:=:advert"],
                "orderBy": "created:desc",
                "page": 1,
                "limit": limit,
            },
        )

    async def get_advert(self, school_id: str, advert_id: str) -> dict[str, Any]:
        return await self.rpc(
            "school.messages.get",
            {"school_id": int(school_id), "id": advert_id, "graph": ["recipients"]},
        )

    async def get_scope_settings(
        self, scope_model: str, scope_id: str, app: str, context: str = "all"
    ) -> dict[str, Any]:
        """Réglages effectifs d'un scope (user/classroom/school) pour une app,
        sans les champs techniques `_stack`/`_contexts`."""
        data = await self.rpc(
            f"{scope_model}.settings.get",
            {f"{scope_model}_id": int(scope_id), "app": app, "context": context},
        )
        return {k: v for k, v in (data or {}).items() if not str(k).startswith("_")}


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}
