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
        _check_allowed(method)
        url = f"{SETTINGS.rpc_base}/{method}"
        resp = await self._http.post(url, json=params or {}, headers=await self._headers())
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
    async def list_classrooms(self) -> Any:
        """Toutes les classes visibles par l'utilisateur authentifié.

        Constat empirique (15/09/2026, testé en conditions réelles par [prénom]) :
        `user.classrooms.fetch` IGNORE tout paramètre de filtrage envoyé — il
        renvoie systématiquement la liste complète des classes accessibles au
        compte (10 classes pour un compte direction sur l'école 11777, alors
        qu'un seul classroom_id avait été demandé). Documenté aussi dans
        cartographie-edumoov.md §6.2.
        """
        return await self.rpc("user.classrooms.fetch", {})

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
        if isinstance(envelope, dict) and "data" in envelope:
            return envelope["data"] or []
        # Forme inattendue : on ne masque pas l'anomalie, on la fait remonter telle
        # quelle plutôt que de risquer un [] silencieux qui cacherait un vrai souci.
        raise EdumoovApiError(
            f"Réponse de /pupils dans un format inattendu (ni enveloppe {{data:...}} "
            f"reconnue) : {type(envelope).__name__}. À vérifier manuellement."
        )

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
    ) -> Any:
        """Endpoint unifié du cartable — cf. cartographie §4 et §6.1.

        `types` couvre : activity, lesson, event, alert, info, meeting, advert,
        code, image, text. `archived=True` -> Archives, `pupil_id`+`achieved` ->
        Suivi des élèves, `type=meeting`+`meetings_start/stop` -> RDV Parents.
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
        return await self.rest_get(f"cartable/classroom/{classroom_id}/messages", params)


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}
