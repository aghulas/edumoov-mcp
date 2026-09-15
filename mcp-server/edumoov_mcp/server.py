"""Serveur MCP Edumoov (prototype).

4 outils prioritaires (voir spec-connecteur-mcp-edumoov.md §7) :
  - edumoov_classrooms_list
  - edumoov_classroom_get
  - edumoov_classroom_pupils_list
  - edumoov_cartable_items_list

Aucun outil d'écriture. Aucun outil n'expose les codes d'accès élève (voir client.py).
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import EdumoovClient
from .config import SETTINGS

mcp = MCPServer(
    name="edumoov",
    title="Edumoov (prototype non officiel)",
    instructions=(
        "Accès en lecture seule à des données Edumoov (école [ecole], [ville]) "
        "via une API non documentée, identifiée par rétro-ingénierie. Prototype personnel "
        "en attendant un accès officiel Edumoov — voir cartographie-edumoov.md et "
        "spec-connecteur-mcp-edumoov.md dans le projet Claude 'Edumoov' pour le contexte "
        "complet, les limitations connues et les règles de sécurité appliquées."
    ),
)

_client: EdumoovClient | None = None


def _get_client() -> EdumoovClient:
    global _client
    if _client is None:
        _client = EdumoovClient()
    return _client


def _redact_pupil(pupil: dict[str, Any]) -> dict[str, Any]:
    """Retire les champs sensibles d'une fiche élève (voir spec §4).

    Champs confirmés (15/09/2026) sur le vrai JSON : voir config.SETTINGS
    .sensitive_pupil_fields et le commentaire de EdumoovClient.list_pupils. Ne
    redacte QUE les champs top-level de la fiche élève ; les emails de parents dans
    pupil["parents"][*]["mail"] ne sont volontairement pas touchés (voir config.py).
    """
    return {k: v for k, v in pupil.items() if k not in SETTINGS.sensitive_pupil_fields}


@mcp.tool()
async def edumoov_classrooms_list() -> Any:
    """Liste toutes les classes visibles par le compte authentifié (pour un compte
    direction : toutes les classes de l'école). Confirmé empiriquement le
    15/09/2026 : l'API ne prend pas de filtre côté serveur sur cet endpoint — voir
    edumoov_classroom_get pour récupérer une seule classe."""
    return await _get_client().list_classrooms()


@mcp.tool()
async def edumoov_classroom_get(classroom_id: str) -> Any:
    """Récupère la fiche d'une classe Edumoov précise (école, niveaux, enseignant,
    effectif...). `classroom_id` est l'identifiant Edumoov interne de la classe
    (visible dans les URLs, ex. '44571'), pas le n° ONDE/IDBE. Filtré côté client à
    partir de edumoov_classrooms_list (l'API ne filtre pas elle-même par id)."""
    return await _get_client().get_classroom(classroom_id)


@mcp.tool()
async def edumoov_classroom_pupils_list(
    classroom_id: str, include_sensitive_fields: bool = False
) -> Any:
    """Liste les élèves d'une classe. Par défaut, `ine` et `birthday` sont retirés
    de chaque fiche élève — passer include_sensitive_fields=True pour les inclure
    explicitement si le cas d'usage le justifie vraiment. Ne redacte PAS les emails
    de parents (pupil["parents"][*]["mail"]) : nécessaires à l'usage principal
    envisagé (contacter une famille). Ne retourne jamais les codes d'accès
    individuels des élèves (endpoint distinct, volontairement exclu)."""
    pupils = await _get_client().list_pupils(classroom_id)
    if include_sensitive_fields or not isinstance(pupils, list):
        return pupils
    return [_redact_pupil(p) if isinstance(p, dict) else p for p in pupils]


@mcp.tool()
async def edumoov_cartable_items_list(
    classroom_id: str,
    types: list[str] | None = None,
    box: str | None = None,
    archived: bool | None = None,
    pupil_id: str | None = None,
    achieved: list[str] | None = None,
    start: str | None = None,
    stop: str | None = None,
    meetings_start: str | None = None,
    meetings_stop: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> Any:
    """Liste les éléments du cartable d'une classe : cahier de liaison, cahier de
    texte (devoirs), cahier de vie (activités), RDV parents, suivi des élèves et
    archives sont tous des vues filtrées du même flux (voir cartographie §4).

    Exemples de filtres utiles :
    - Cahier de liaison envoyé : box='sent', types=['activity']
    - Devoirs à venir : types=['lesson'], start=<date ISO>, stop=<date ISO>
    - RDV parents : types=['meeting'], meetings_start=<date ISO>, meetings_stop=<date ISO>
    - Suivi d'un élève : pupil_id=<id>, achieved=['done','todo']
    - Archives : archived=True
    """
    return await _get_client().list_cartable_items(
        classroom_id,
        types=types,
        box=box,
        archived=archived,
        pupil_id=pupil_id,
        achieved=achieved,
        start=start,
        stop=stop,
        meetings_start=meetings_start,
        meetings_stop=meetings_stop,
        sort=sort,
        direction=direction,
        page=page,
        limit=limit,
    )
