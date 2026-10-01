"""Outils MCP d'écriture Edumoov (30/09/2026) — tous en deux temps.

Chaque outil `edumoov_*_prepare_*` ne modifie RIEN : il vérifie la demande,
construit l'appel RPC exact et renvoie un aperçu + un jeton. L'écriture n'a lieu
qu'avec `edumoov_write_confirm(jeton)`, après accord explicite de l'utilisateur.
Voir writes.py pour les verrous (EDUMOOV_ENABLE_WRITES, expiration, liste
blanche) et cartographie-edumoov.md §6.11 pour l'origine de chaque méthode RPC.
"""
from __future__ import annotations

import datetime
import html
from typing import Any

from .client import EdumoovApiError
from .config import SETTINGS
from .server import _get_client, mcp
from .writes import WriteGate, diff_leaves, log_outcome

_gate = WriteGate()

ADVERT_BODY_MAX = 5000  # maxlength de l'éditeur d'annonce côté front
KNOWN_SETTINGS_APPS = (
    "Educartable",
    "EducartableFamily",
    "_common",
    "Livret",
    "Journal",
    "Appel",
    "Classe",
)
SETTINGS_SCOPES = ("user", "classroom", "school")
# Chemins de réglages jamais modifiables par ce connecteur : la signature
# numérisée de direction a une valeur probatoire (voir cartographie §6.5).
FORBIDDEN_SETTINGS_MARKERS = ("signatureFile",)
# Champs de fiche classe modifiables (vérifiés présents dans user.classrooms.fetch).
CLASSROOM_EDITABLE_FIELDS = (
    "name",
    "inc",
    "cartable_activated",
    "is_director_allowed",
    "teacher_name",
    "teacher_gender",
)
KNOWN_ROLES = ("TIT", "DECL", "REMP", "ATSEM", "AESH", "DIR")


def _school_id(school_id: str | None) -> str:
    resolved = school_id or SETTINGS.default_school_id
    if not resolved:
        raise ValueError("school_id requis (ou EDUMOOV_DEFAULT_SCHOOL_ID).")
    return str(resolved)


def _advert_status(visibility: str | None) -> str:
    if not visibility:
        return "brouillon"
    try:
        when = datetime.datetime.fromisoformat(str(visibility).replace("Z", "+00:00"))
    except ValueError:
        return "publiée"
    return "publiée" if when <= datetime.datetime.now(datetime.timezone.utc) else "programmée"


def _iso_utc(value: str) -> str:
    """'now' ou date/heure ISO (heure de Paris si sans fuseau) → ISO UTC 'Z',
    format envoyé par le front (JSON d'un objet Date)."""
    if value == "now":
        when = datetime.datetime.now(datetime.timezone.utc)
    else:
        when = datetime.datetime.fromisoformat(value)
        if when.tzinfo is None:
            from zoneinfo import ZoneInfo

            when = when.replace(tzinfo=ZoneInfo("Europe/Paris"))
    return when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def text_to_html(text: str) -> str:
    """Texte brut → HTML simple (le corps des annonces est stocké en HTML).
    Paragraphes séparés par une ligne vide, retours à la ligne en <br>."""
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in p.split("\n")) + "</p>"
        for p in paragraphs
    )


async def _school_classrooms(school_id: str) -> dict[int, str]:
    classrooms = await _get_client().list_classrooms()
    return {
        int(c["id"]): c.get("name") or str(c["id"])
        for c in classrooms
        if str(c.get("school_id")) == str(school_id)
        and not c.get("release")
        and not c.get("deleted")
    }


async def _resolve_recipients(
    school_id: str, classroom_ids: list[str] | None
) -> tuple[list[int], dict[int, str]]:
    classrooms = await _school_classrooms(school_id)
    if classroom_ids is None:
        return sorted(classrooms), classrooms
    ids = [int(x) for x in classroom_ids]
    unknown = [i for i in ids if i not in classrooms]
    if unknown:
        raise ValueError(
            f"Classe(s) inconnue(s) pour l'école {school_id} : {unknown}. "
            "Utiliser edumoov_classrooms_list pour les identifiants."
        )
    return ids, classrooms


def _advert_summary(a: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    recipients = [
        names.get(int(r["key"]), f"classe {r['key']}")
        for r in a.get("recipients") or []
        if r.get("model") == "Classroom"
    ]
    return {
        "id": a.get("id"),
        "title": a.get("title"),
        "status": _advert_status(a.get("visibility")),
        "visibility": a.get("visibility"),
        "created": a.get("created"),
        "modified": a.get("modified"),
        "recipients": recipients,
    }


# ----------------------------------------------------------------------
# Lecture utile aux écritures
# ----------------------------------------------------------------------
@mcp.tool()
async def edumoov_adverts_list(
    school_id: str | None = None, limit: int = 20, include_body: bool = False
) -> Any:
    """Liste les annonces d'école (brouillons, programmées, publiées) avec leurs
    classes destinataires. `include_body=True` ajoute le corps HTML."""
    sid = _school_id(school_id)
    adverts = await _get_client().list_adverts(sid, limit=limit)
    names = await _school_classrooms(sid)
    out = []
    for a in adverts or []:
        item = _advert_summary(a, names)
        if include_body:
            item["body"] = a.get("body")
        out.append(item)
    return out


@mcp.tool()
async def edumoov_settings_get(
    scope_model: str, scope_id: str, app: str, context: str = "all"
) -> Any:
    """Réglages effectifs d'un utilisateur, d'une classe ou d'une école pour une
    app Edumoov. `scope_model` : user | classroom | school. `app` : Educartable,
    EducartableFamily, _common, Livret, Journal, Appel, Classe."""
    if scope_model not in SETTINGS_SCOPES:
        raise ValueError(f"scope_model doit être l'un de {SETTINGS_SCOPES}")
    return await _get_client().get_scope_settings(scope_model, scope_id, app, context)


# ----------------------------------------------------------------------
# Annonces d'école
# ----------------------------------------------------------------------
@mcp.tool()
async def edumoov_advert_prepare_create(
    title: str,
    body: str,
    school_id: str | None = None,
    classroom_ids: list[str] | None = None,
    publication: str = "draft",
    body_is_html: bool = False,
) -> Any:
    """PRÉPARE (n'envoie rien) une annonce d'école aux familles.

    - `classroom_ids` : classes destinataires ; None = toutes les classes actives
      de l'école (comportement par défaut de l'interface Edumoov).
    - `publication` : "draft" (défaut, brouillon invisible des familles),
      "now" (publication immédiate + notifications aux familles), ou une date ISO
      (ex. "2026-10-05T08:00", heure de Paris) pour une publication programmée.
    - `body` en texte brut (converti en HTML) sauf si `body_is_html=True`.
    Renvoie un aperçu et un jeton à passer à edumoov_write_confirm après accord."""
    sid = _school_id(school_id)
    if not title.strip():
        raise ValueError("Le titre est obligatoire.")
    body_html = body if body_is_html else text_to_html(body)
    if len(body_html) > ADVERT_BODY_MAX:
        raise ValueError(f"Corps trop long ({len(body_html)} > {ADVERT_BODY_MAX} caractères).")
    ids, names = await _resolve_recipients(sid, classroom_ids)
    visibility = None if publication == "draft" else _iso_utc(publication)
    if visibility and not ids:
        raise ValueError("Impossible de publier une annonce sans classe destinataire.")
    payload = {
        "title": title.strip(),
        "body": body_html,
        "icon": "fas fa-bell",
        "color": "red-6",
        "recipients": [{"model": "Classroom", "key": i} for i in ids],
        "type": "advert",
        "visibility": visibility,
    }
    status = _advert_status(visibility)
    warnings = []
    if status == "publiée":
        warnings.append(
            f"Publication IMMÉDIATE : visible et notifiée aux familles de {len(ids)} classe(s)."
        )
    elif status == "programmée":
        warnings.append(
            f"Publication programmée le {visibility} (UTC) pour {len(ids)} classe(s)."
        )
    return _gate.prepare(
        "school.messages.create",
        {"school_id": int(sid), "graph": ["recipients"]},
        payload,
        summary=f"Créer l'annonce « {payload['title']} » ({status}) pour {len(ids)} classe(s)",
        preview={
            "title": payload["title"],
            "body_html": body_html,
            "status": status,
            "recipients": [names[i] for i in ids],
        },
        warnings=warnings,
    )


@mcp.tool()
async def edumoov_advert_prepare_update(
    advert_id: str,
    school_id: str | None = None,
    title: str | None = None,
    body: str | None = None,
    body_is_html: bool = False,
    classroom_ids: list[str] | None = None,
) -> Any:
    """PRÉPARE la modification d'une annonce existante (titre, corps, classes
    destinataires). Ne change pas son statut de publication — voir
    edumoov_advert_prepare_visibility. Aperçu avant → après + jeton."""
    sid = _school_id(school_id)
    client = _get_client()
    current = await client.get_advert(sid, advert_id)
    names = await _school_classrooms(sid)
    before = _advert_summary(current, names)
    payload: dict[str, Any] = {}
    changes = []
    if title is not None and title.strip() != current.get("title"):
        payload["title"] = title.strip()
        changes.append({"field": "title", "before": current.get("title"), "after": title.strip()})
    if body is not None:
        body_html = body if body_is_html else text_to_html(body)
        if len(body_html) > ADVERT_BODY_MAX:
            raise ValueError(f"Corps trop long ({len(body_html)} > {ADVERT_BODY_MAX}).")
        if body_html != current.get("body"):
            payload["body"] = body_html
            changes.append({"field": "body", "before": current.get("body"), "after": body_html})
    if classroom_ids is not None:
        ids, _ = await _resolve_recipients(sid, classroom_ids)
        payload["recipients"] = [{"model": "Classroom", "key": i} for i in ids]
        changes.append(
            {"field": "recipients", "before": before["recipients"], "after": [names[i] for i in ids]}
        )
    if not payload:
        raise ValueError("Aucun changement par rapport à l'annonce actuelle.")
    warnings = []
    if before["status"] != "brouillon":
        warnings.append(
            f"Annonce déjà {before['status']} : la modification sera visible des familles."
        )
    return _gate.prepare(
        "school.messages.update",
        {"school_id": int(sid), "id": advert_id, "graph": ["recipients"]},
        payload,
        summary=f"Modifier l'annonce « {current.get('title')} » ({before['status']})",
        preview={"changes": changes},
        warnings=warnings,
    )


@mcp.tool()
async def edumoov_advert_prepare_visibility(
    advert_id: str, action: str, school_id: str | None = None, at: str | None = None
) -> Any:
    """PRÉPARE la publication d'une annonce. `action` :
    - "publish" : publier maintenant (notifie les familles des classes destinataires) ;
    - "schedule" : programmer à `at` (date ISO, heure de Paris si sans fuseau) ;
    - "unpublish" : repasser en brouillon (retirée de la vue des familles)."""
    sid = _school_id(school_id)
    current = await _get_client().get_advert(sid, advert_id)
    names = await _school_classrooms(sid)
    before = _advert_summary(current, names)
    if action == "publish":
        visibility = _iso_utc("now")
    elif action == "schedule":
        if not at:
            raise ValueError("`at` est requis pour action='schedule'.")
        visibility = _iso_utc(at)
    elif action == "unpublish":
        visibility = None
    else:
        raise ValueError("action doit être 'publish', 'schedule' ou 'unpublish'.")
    if visibility and not before["recipients"]:
        raise ValueError("Cette annonce n'a aucune classe destinataire : la compléter d'abord.")
    after_status = _advert_status(visibility)
    warnings = []
    if after_status == "publiée":
        warnings.append(
            f"Publication IMMÉDIATE aux familles de {len(before['recipients'])} classe(s) : "
            + ", ".join(before["recipients"])
        )
    return _gate.prepare(
        "school.messages.update",
        {"school_id": int(sid), "id": advert_id, "graph": ["recipients"]},
        {"visibility": visibility},
        summary=f"Annonce « {current.get('title')} » : {before['status']} → {after_status}",
        preview={"before": before, "after_status": after_status, "visibility": visibility},
        warnings=warnings,
    )


@mcp.tool()
async def edumoov_advert_prepare_delete(advert_id: str, school_id: str | None = None) -> Any:
    """PRÉPARE la suppression d'une annonce d'école (les familles n'y auront plus
    accès si elle était publiée). Aperçu + jeton."""
    sid = _school_id(school_id)
    current = await _get_client().get_advert(sid, advert_id)
    names = await _school_classrooms(sid)
    before = _advert_summary(current, names)
    warnings = []
    if before["status"] != "brouillon":
        warnings.append("Annonce déjà diffusée : les familles perdront l'accès à son contenu.")
    return _gate.prepare(
        "school.messages.delete",
        {"school_id": int(sid), "id": advert_id},
        {},
        summary=f"Supprimer l'annonce « {current.get('title')} » ({before['status']})",
        preview=before,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Paramètres (réglages utilisateur / classe / école)
# ----------------------------------------------------------------------
@mcp.tool()
async def edumoov_settings_prepare_set(
    scope_model: str,
    scope_id: str,
    app: str,
    changes: dict[str, Any],
    context: str | None = None,
) -> Any:
    """PRÉPARE une modification de réglages Edumoov (fusion partielle, comme
    l'interface) pour un utilisateur, une classe ou une école.

    - `scope_model` : user | classroom | school ; `scope_id` : son identifiant.
    - `app` : Educartable, EducartableFamily, _common, Livret, Journal, Appel, Classe.
    - `changes` : objet partiel, ex. {"ui": {"precision": 10}} ou
      {"features": {"LivretBelts": false}}. Lire d'abord edumoov_settings_get
      (ou edumoov_user_settings_get / edumoov_classroom_settings_get).
    Aperçu chemin par chemin (avant → après) + jeton."""
    if scope_model not in SETTINGS_SCOPES:
        raise ValueError(f"scope_model doit être l'un de {SETTINGS_SCOPES}")
    if app not in KNOWN_SETTINGS_APPS:
        raise ValueError(f"app inconnue : {app!r}. Valeurs : {KNOWN_SETTINGS_APPS}")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("`changes` doit être un objet non vide.")
    current = await _get_client().get_scope_settings(scope_model, scope_id, app)
    diff = diff_leaves(current, changes)
    for change in diff:
        if any(marker in change["path"] for marker in FORBIDDEN_SETTINGS_MARKERS):
            raise ValueError(
                f"Chemin non modifiable par ce connecteur : {change['path']} "
                "(signature numérisée, valeur probatoire)."
            )
    if not diff:
        raise ValueError("Aucun changement : les valeurs demandées sont déjà en place.")
    warnings = []
    if scope_model != "user":
        warnings.append(
            f"Réglage partagé : s'applique à toute la {'classe' if scope_model == 'classroom' else 'école'}."
        )
    return _gate.prepare(
        f"{scope_model}.settings.set",
        {f"{scope_model}_id": int(scope_id), "app": app, "context": context},
        changes,
        summary=f"Modifier {len(diff)} réglage(s) {app} de {scope_model} {scope_id}",
        preview={"changes": diff},
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Structure école / classes
# ----------------------------------------------------------------------
@mcp.tool()
async def edumoov_classroom_prepare_update(
    classroom_id: str, changes: dict[str, Any], school_id: str | None = None
) -> Any:
    """PRÉPARE la modification d'une fiche classe. Champs autorisés : name, inc
    (n° ONDE/IDBE), cartable_activated, is_director_allowed, teacher_name,
    teacher_gender. Aperçu avant → après + jeton."""
    sid = _school_id(school_id)
    bad = [k for k in changes if k not in CLASSROOM_EDITABLE_FIELDS]
    if bad:
        raise ValueError(f"Champ(s) non modifiable(s) : {bad}. Autorisés : {CLASSROOM_EDITABLE_FIELDS}")
    classroom = await _get_client().get_classroom(classroom_id)
    if str(classroom.get("school_id")) != sid:
        raise ValueError(f"La classe {classroom_id} n'appartient pas à l'école {sid}.")
    diff = [
        {"field": k, "before": classroom.get(k), "after": v}
        for k, v in changes.items()
        if classroom.get(k) != v
    ]
    if not diff:
        raise ValueError("Aucun changement par rapport à la fiche actuelle.")
    payload = {"id": int(classroom_id), **{d["field"]: d["after"] for d in diff}}
    return _gate.prepare(
        "school.classrooms.update",
        {"school_id": int(sid), "id": int(classroom_id)},
        payload,
        summary=f"Modifier la classe « {classroom.get('name')} » ({len(diff)} champ(s))",
        preview={"changes": diff},
    )


@mcp.tool()
async def edumoov_classroom_prepare_teacher(
    classroom_id: str, user_id: str, action: str, roles: list[str] | None = None
) -> Any:
    """PRÉPARE l'affectation (action="link", avec `roles`, défaut ["TIT"]) ou le
    retrait (action="unlink") d'un enseignant sur une classe. Rôles connus :
    TIT (titulaire), DECL (décharge), REMP (remplaçant), ATSEM, AESH, DIR.
    ⚠️ Méthode lue dans le code front mais pas encore validée en conditions réelles."""
    classroom = await _get_client().get_classroom(classroom_id)
    teachers = {str(t.get("id")): t for t in await _get_client().list_school_teachers(str(classroom.get("school_id")))}
    teacher = teachers.get(str(user_id))
    who = f"{teacher.get('firstname', '')} {teacher.get('name', '')}".strip() if teacher else f"utilisateur {user_id}"
    cid = int(classroom_id)
    warnings = ["Méthode non encore validée en conditions réelles : vérifier le résultat dans Edumoov."]
    if action == "link":
        roles = roles or ["TIT"]
        bad = [r for r in roles if r not in KNOWN_ROLES]
        if bad:
            raise ValueError(f"Rôle(s) inconnu(s) : {bad}. Connus : {KNOWN_ROLES}")
        if teacher is None:
            warnings.append("Cet utilisateur n'apparaît pas dans l'annuaire enseignants de l'école.")
        payload: dict[str, Any] = {"related": {"id": int(user_id), "_linkProps": {"roles": roles}}}
        method = "classroom.classrooms.link"
        summary = f"Affecter {who} à « {classroom.get('name')} » avec le(s) rôle(s) {roles}"
    elif action == "unlink":
        payload = {"related": [int(user_id)]}
        method = "classroom.classrooms.unlink"
        summary = f"Retirer {who} de la classe « {classroom.get('name')} »"
        warnings.append("Cette personne perdra l'accès à la classe dans Edumoov.")
    else:
        raise ValueError("action doit être 'link' ou 'unlink'.")
    return _gate.prepare(
        method,
        {"classroom_id": cid, "id": cid, "relation": "users"},
        payload,
        summary=summary,
        preview={"classroom": classroom.get("name"), "user": who, "roles": roles if action == "link" else None},
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Confirmation / annulation
# ----------------------------------------------------------------------
@mcp.tool()
async def edumoov_write_confirm(confirmation_token: str) -> Any:
    """EXÉCUTE une écriture préparée par un outil *_prepare_*. À n'appeler
    qu'après avoir montré l'aperçu à l'utilisateur et obtenu son accord
    explicite. Jeton à usage unique, valable 10 minutes."""
    pending = _gate.pop(confirmation_token)
    try:
        data = await _get_client().rpc_write(pending.method, pending.params, pending.payload)
    except EdumoovApiError:
        log_outcome(pending.method, False)
        raise
    log_outcome(pending.method, True)
    result: dict[str, Any] = {"status": "effectué", "summary": pending.summary}
    if pending.method.startswith("school.messages.") and isinstance(data, dict):
        result["advert"] = {
            "id": data.get("id"),
            "title": data.get("title"),
            "status": _advert_status(data.get("visibility")),
            "visibility": data.get("visibility"),
        }
    return result


@mcp.tool()
async def edumoov_write_cancel(confirmation_token: str) -> Any:
    """Annule une écriture préparée (le jeton devient inutilisable)."""
    return {"cancelled": _gate.cancel(confirmation_token)}


@mcp.tool()
async def edumoov_write_pending_list() -> Any:
    """Écritures préparées en attente de confirmation (et état du verrou global)."""
    return {"writes_enabled": _gate.enabled, "pending": _gate.list_pending()}
