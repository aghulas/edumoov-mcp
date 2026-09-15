"""Serveur MCP Edumoov (prototype).

20 outils (voir spec-connecteur-mcp-edumoov.md §3) :
  - edumoov_classrooms_list, edumoov_classroom_get, edumoov_classroom_pupils_list,
    edumoov_cartable_items_list (implémentés et testés en premier)
  - edumoov_school_get, edumoov_school_teachers_list, edumoov_grades_list,
    edumoov_classroom_events_list, edumoov_user_settings_get (RPC)
  - edumoov_classroom_recipients_list, edumoov_user_notifications_list,
    edumoov_cartable_item_comments_list, edumoov_preps_sequences_search,
    edumoov_web2print_books_list (REST)
  - edumoov_evaluations_list, edumoov_assessments_list, edumoov_mater_skills_list,
    edumoov_belts_list, edumoov_classroom_settings_get (Livret — RPC via /rpc/
    HTTP, découvert par capture du canal Socket.IO, voir client.py)
  - edumoov_media_get_url (résolution d'id média en URL signée — ⚠️ endpoint dont
    le contrôle d'accès s'est révélé faible en test, voir client.py:get_media_url)

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
    if not isinstance(pupils, list):
        # Voir l'incident documenté dans cartographie-edumoov.md §9 : une redaction
        # qui "passe" silencieusement sur une forme de donnée inattendue est
        # exactement le bug qui a exposé 22 fiches élève en clair. list_pupils()
        # est censé garantir une vraie liste (voir _unwrap_rest_envelope côté
        # client.py) ; si ce n'est plus le cas, on refuse de deviner plutôt que de
        # risquer un retour non redacté en silence — corriger côté client.py, pas
        # contourner ici. Couvert par tests/test_redaction.py.
        raise TypeError(
            "edumoov_classroom_pupils_list : list_pupils() a renvoyé "
            f"{type(pupils).__name__} au lieu d'une liste — refus de retourner un "
            "résultat potentiellement non redacté. Voir cartographie-edumoov.md §9."
        )
    if include_sensitive_fields:
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


@mcp.tool()
async def edumoov_school_get(school_id: str) -> Any:
    """Fiche d'un établissement (adresse, UAI, directeur...). `school_id` est
    l'identifiant Edumoov interne de l'école (ex. '11777' pour [ecole])."""
    return await _get_client().get_school(school_id)


@mcp.tool()
async def edumoov_school_teachers_list(school_id: str) -> Any:
    """Annuaire des enseignants d'une école (id, nom, prénom, email)."""
    return await _get_client().list_school_teachers(school_id)


@mcp.tool()
async def edumoov_grades_list() -> Any:
    """Référentiel national des niveaux scolaires (TPS à CM2, avec cycle). Pas de
    paramètre — c'est un référentiel global, pas une donnée liée à un compte."""
    return await _get_client().list_grades()


@mcp.tool()
async def edumoov_classroom_events_list(
    classroom_id: str,
    start: str | None = None,
    stop: str | None = None,
    query: list[str] | None = None,
    graph: list[str] | None = None,
    page: int = 1,
) -> Any:
    """Événements/créneaux d'une classe (calendrier).

    Cet endpoint RPC exige des paramètres (pas seulement classroom_id) — voir
    client.py::list_classroom_events. Par défaut : fenêtre de 30 jours à partir
    d'aujourd'hui, événements confirmés (status BOOKED)."""
    return await _get_client().list_classroom_events(
        classroom_id, start=start, stop=stop, query=query, graph=graph, page=page
    )


@mcp.tool()
async def edumoov_user_settings_get(user_id: str | None = None, app: str = "educartable") -> Any:
    """Préférences de l'utilisateur authentifié (notifications, UI, favoris, vue
    par défaut). Exige un id utilisateur explicite (retombe sur
    EDUMOOV_DEFAULT_USER_ID si non fourni) et le nom de l'app concernée
    (défaut "educartable") — voir client.py::get_user_settings."""
    return await _get_client().get_user_settings(user_id=user_id, app=app)


@mcp.tool()
async def edumoov_classroom_recipients_list(classroom_id: str) -> Any:
    """Destinataires (parents/contacts) d'une classe."""
    return await _get_client().list_recipients(classroom_id)


@mcp.tool()
async def edumoov_user_notifications_list(
    user_id: str, app: str | None = None, scope: str | None = None, limit: int | None = None
) -> Any:
    """Notifications d'un utilisateur. `app` observé en capture : 'Educartable'."""
    return await _get_client().list_notifications(user_id, app=app, scope=scope, limit=limit)


@mcp.tool()
async def edumoov_cartable_item_comments_list(classroom_id: str, key: str) -> Any:
    """Fil de commentaires sur un élément du cartable. `key` = l'`id` (ou uuid) d'un
    élément renvoyé par edumoov_cartable_items_list."""
    return await _get_client().list_item_comments(classroom_id, key)


@mcp.tool()
async def edumoov_preps_sequences_search(user_id: str, query: str | None = None) -> Any:
    """Recherche dans la bibliothèque de séquences pédagogiques (« Fiches de
    préparation, séquences » — probablement une bibliothèque partagée, pas
    propre à l'école, voir cartographie §2)."""
    return await _get_client().search_preps_sequences(user_id, query=query)


@mcp.tool()
async def edumoov_web2print_books_list(classroom_id: str) -> Any:
    """Livres/exports photo d'une classe (cahier de vie)."""
    return await _get_client().list_web2print_books(classroom_id)


@mcp.tool()
async def edumoov_evaluations_list(
    classroom_id: str,
    query: list[str] | None = None,
    graph: list[str] | None = None,
    order_by: str = "date:desc",
    page: int = 1,
    limit: int = 100,
) -> Any:
    """Évaluations du Livret (notes/résultats par matière et par période).
    `query` ex. : ["where:date:>=:2026-01-01", "where:date:<:2026-07-01"]."""
    return await _get_client().list_evaluations(
        classroom_id, query=query, graph=graph, order_by=order_by, page=page, limit=limit
    )


@mcp.tool()
async def edumoov_assessments_list(
    classroom_id: str,
    start: str | None = None,
    stop: str | None = None,
    query: list[str] | None = None,
    page: int = 1,
    limit: int = 100,
) -> Any:
    """Suivi de compétences par élève (section « Suivi des élèves » du Livret).
    `query` ex. pour un seul élève : ["where:pupil_id:=:<id>"]."""
    return await _get_client().list_assessments(
        classroom_id, start=start, stop=stop, query=query, page=page, limit=limit
    )


@mcp.tool()
async def edumoov_mater_skills_list(
    classroom_id: str,
    query: list[str] | None = None,
    graph: list[str] | None = None,
    order_by: str = "date:desc",
    page: int = 1,
    limit: int = 100,
) -> Any:
    """Réussites maternelle (badges/réussites par domaine, classes de maternelle)."""
    return await _get_client().list_mater_skills(
        classroom_id, query=query, graph=graph, order_by=order_by, page=page, limit=limit
    )


@mcp.tool()
async def edumoov_belts_list(
    classroom_id: str, page: int = 1, limit: int = 500, graph: list[str] | None = None
) -> Any:
    """Ceintures de compétences activées pour la classe (vide si le système de
    ceintures n'est pas activé — voir edumoov_classroom_settings_get)."""
    return await _get_client().list_belts(classroom_id, page=page, limit=limit, graph=graph)


@mcp.tool()
async def edumoov_classroom_settings_get(
    classroom_id: str, app: str = "Livret", context: str = "all"
) -> Any:
    """Configuration Livret de la classe : fonctionnalités activées et barème
    de notation (utile pour interpréter les évaluations)."""
    return await _get_client().get_classroom_settings(classroom_id, app=app, context=context)


@mcp.tool()
async def edumoov_media_get_url(media_id: str, token: str | None = None) -> str:
    """Résout un id de média Edumoov (ex. `logoFile`/`signatureFile` renvoyés par
    edumoov_classroom_settings_get, ou tout autre média référencé ailleurs) en une
    URL de téléchargement signée et temporaire.

    ⚠️ Le paramètre `token` est optionnel et, d'après les tests effectués, n'est
    pas vérifié par l'API pour les médias déjà testés : passe-le quand tu l'as
    (il ne coûte rien), mais ne compte pas dessus comme un vrai contrôle d'accès.
    Voir client.py:get_media_url et cartographie-edumoov.md §6.2 pour le détail
    et l'implication sécurité (accès potentiellement non authentifié par id)."""
    return await _get_client().get_media_url(media_id, token=token)
