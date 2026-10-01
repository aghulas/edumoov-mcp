"""Outils MCP d'écriture du Cartable (01/10/2026) : cahier de liaison d'une classe
et commentaires — toujours en deux temps, comme write_tools.py.

Endpoints REST legacy (www.edumoov.com/api/1.0), lus dans le bundle
static.edumoov.com/cartable puis validés en réel sur un brouillon sans
destinataire (spec-ecriture-edumoov.md §6.4) :
- POST   cartable/classroom/{id}/messages            → crée (brouillon : visible=false)
- PUT    cartable/classroom/{id}/messages/{uuid}     → modifie / publie ({visible:true})
- DELETE cartable/classroom/{id}/messages/{uuid}     → supprime
- POST   core/classroom/{id}/comments                → commente (key, model, body, reply)
- DELETE core/classroom/{id}/comments/{id}/trash     → supprime un commentaire

Règles : un message est TOUJOURS créé en brouillon (invisible des familles) ; la
publication est une écriture séparée (edumoov_cahier_prepare_visibility), avec
avertissement explicite sur le nombre d'élèves dont les familles seront
notifiées. Destinataires limités aux élèves de la classe. Classe limitée aux
classes actives de l'école. Logs sans contenu (writes.py).
"""
from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .server import _get_client, mcp
from . import write_tools as _wt
from .write_tools import _school_classrooms, _school_id

CAHIER_TYPES = ("info", "alert")  # types observés pour le cahier de liaison
ACKNOWLEDGEMENTS = {"none": "", "read": "read", "comment": "comment"}
CAHIER_TITLE_MAX = 255
CAHIER_BODY_MAX = 10000
COMMENT_BODY_MAX = 5000


def _now_paris() -> str:
    return datetime.datetime.now(ZoneInfo("Europe/Paris")).isoformat(timespec="seconds")


async def _check_classroom(classroom_id: str, school_id: str | None) -> str:
    sid = _school_id(school_id)
    classrooms = await _school_classrooms(sid)
    cid = int(classroom_id)
    if cid not in classrooms:
        raise ValueError(
            f"Classe {classroom_id} inconnue ou inactive pour l'école {sid}. "
            "Utiliser edumoov_classrooms_list pour les identifiants."
        )
    return classrooms[cid]


def _pupil_name(p: dict[str, Any]) -> str:
    return p.get("fullname") or f"{p.get('firstname', '')} {p.get('name', '')}".strip() or str(p.get("id"))


async def _resolve_pupils(classroom_id: str, pupil_ids: list[str] | None) -> list[dict[str, Any]]:
    """Destinataires au format du front : [{type:"Pupil", value:<id élève>, name}].
    None = toute la classe ; [] = aucun destinataire (brouillon de travail)."""
    pupils = {int(p["id"]): p for p in await _get_client().list_pupils(classroom_id)}
    if pupil_ids is None:
        ids = sorted(pupils, key=lambda i: _pupil_name(pupils[i]))
    else:
        ids = [int(x) for x in pupil_ids]
        unknown = [i for i in ids if i not in pupils]
        if unknown:
            raise ValueError(
                f"Élève(s) absent(s) de la classe {classroom_id} : {unknown}. "
                "Utiliser edumoov_classroom_pupils_list pour les identifiants."
            )
    return [{"type": "Pupil", "value": i, "name": _pupil_name(pupils[i])} for i in ids]


def _status(message: dict[str, Any]) -> str:
    return "publié" if message.get("visible") else "brouillon"


def _message_summary(m: dict[str, Any]) -> dict[str, Any]:
    recipients = m.get("recipients") or []
    return {
        "id": m.get("id"),
        "type": m.get("type"),
        "title": m.get("title"),
        "status": _status(m),
        "date": m.get("date"),
        "commentable": m.get("commentable"),
        "acknowledgement": m.get("achievement") or "none",
        "recipients_count": len(recipients),
        "recipients": [r.get("name") for r in recipients],
    }


def _validate_text(title: str | None, body: str | None) -> None:
    if title is not None:
        if not title.strip():
            raise ValueError("Le titre est obligatoire.")
        if len(title) > CAHIER_TITLE_MAX:
            raise ValueError(f"Titre trop long ({len(title)} > {CAHIER_TITLE_MAX}).")
    if body is not None and len(body) > CAHIER_BODY_MAX:
        raise ValueError(f"Corps trop long ({len(body)} > {CAHIER_BODY_MAX} caractères).")


def _ack(value: str) -> str:
    if value not in ACKNOWLEDGEMENTS:
        raise ValueError(f"acknowledgement doit être l'un de {tuple(ACKNOWLEDGEMENTS)}")
    return ACKNOWLEDGEMENTS[value]


async def _get_message(classroom_id: str, message_id: str) -> dict[str, Any]:
    message = await _get_client().get_cartable_message(classroom_id, message_id)
    if str(message.get("id")) != str(message_id):
        raise ValueError(f"Message {message_id} introuvable dans la classe {classroom_id}.")
    scope_key = message.get("scope_key")
    if scope_key is not None and str(scope_key) != str(classroom_id):
        raise ValueError(f"Le message {message_id} n'appartient pas à la classe {classroom_id}.")
    return message


def _messages_path(classroom_id: str, message_id: str | None = None) -> str:
    base = f"cartable/classroom/{int(classroom_id)}/messages"
    return f"{base}/{message_id}" if message_id else base


# ----------------------------------------------------------------------
# Cahier de liaison — messages de classe
# ----------------------------------------------------------------------
@mcp.tool()
async def edumoov_cahier_prepare_create(
    classroom_id: str,
    title: str,
    body: str,
    pupil_ids: list[str] | None = None,
    message_type: str = "info",
    commentable: bool = True,
    acknowledgement: str = "none",
    school_id: str | None = None,
) -> Any:
    """PRÉPARE (n'envoie rien) un message du cahier de liaison d'une classe.

    Le message est TOUJOURS créé en BROUILLON (invisible des familles, aucune
    notification) ; le publier ensuite avec edumoov_cahier_prepare_visibility.
    - `pupil_ids` : élèves destinataires ; None = toute la classe ; [] = aucun.
    - `message_type` : "info" (message, défaut) ou "alert" (mot important).
    - `commentable` : les familles peuvent répondre en commentaire.
    - `acknowledgement` : "none", "read" (accusé de lecture demandé) ou
      "comment" (réponse demandée).
    - `body` en texte brut (format de la quasi-totalité des messages existants).
    Renvoie un aperçu et un jeton à passer à edumoov_write_confirm après accord."""
    classroom_name = await _check_classroom(classroom_id, school_id)
    _validate_text(title, body)
    if message_type not in CAHIER_TYPES:
        raise ValueError(f"message_type doit être l'un de {CAHIER_TYPES}")
    achievement = _ack(acknowledgement)
    recipients = await _resolve_pupils(classroom_id, pupil_ids)
    payload = {
        "title": title.strip(),
        "body": body,
        "visible": False,
        "visibility": None,
        "recipients": recipients,
        "type": message_type,
        "commentable": bool(commentable),
        "achievement": achievement,
        "subject": None,
        "subject_id": None,
        "games": [],
        "date": _now_paris(),
        "links": [],
        "revisions": [],
        "color": None,
        "icon": "fas fa-bell",
        "pupil_present": False,
        "survey": None,
    }
    warnings = []
    if not recipients:
        warnings.append("Aucun destinataire : il faudra en ajouter avant de pouvoir publier.")
    return _wt._gate.prepare(
        _messages_path(classroom_id),
        {"classroom_id": int(classroom_id)},
        payload,
        summary=(
            f"Créer en brouillon le message « {payload['title']} » ({message_type}) "
            f"dans le cahier de liaison de {classroom_name}, {len(recipients)} élève(s) destinataire(s)"
        ),
        preview={
            "classroom": classroom_name,
            "title": payload["title"],
            "body": body,
            "type": message_type,
            "status": "brouillon",
            "commentable": payload["commentable"],
            "acknowledgement": acknowledgement,
            "recipients_count": len(recipients),
            "recipients": [r["name"] for r in recipients],
        },
        warnings=warnings,
        transport="rest",
        http_method="POST",
    )


@mcp.tool()
async def edumoov_cahier_prepare_update(
    classroom_id: str,
    message_id: str,
    title: str | None = None,
    body: str | None = None,
    pupil_ids: list[str] | None = None,
    commentable: bool | None = None,
    acknowledgement: str | None = None,
    school_id: str | None = None,
) -> Any:
    """PRÉPARE la modification d'un message du cahier de liaison (titre, corps,
    élèves destinataires, commentaires autorisés, accusé demandé). Ne change pas
    son statut de publication — voir edumoov_cahier_prepare_visibility.
    `pupil_ids` remplace toute la liste des destinataires s'il est fourni.
    Aperçu avant → après + jeton."""
    classroom_name = await _check_classroom(classroom_id, school_id)
    _validate_text(title, body)
    current = await _get_message(classroom_id, message_id)
    payload: dict[str, Any] = {}
    changes: list[dict[str, Any]] = []
    if title is not None and title.strip() != current.get("title"):
        payload["title"] = title.strip()
        changes.append({"field": "title", "before": current.get("title"), "after": payload["title"]})
    if body is not None and body != current.get("body"):
        payload["body"] = body
        changes.append({"field": "body", "before": current.get("body"), "after": body})
    if pupil_ids is not None:
        recipients = await _resolve_pupils(classroom_id, pupil_ids)
        before_ids = sorted(int(r.get("value")) for r in current.get("recipients") or [])
        if sorted(r["value"] for r in recipients) != before_ids:
            payload["recipients"] = recipients
            changes.append(
                {
                    "field": "recipients",
                    "before": [r.get("name") for r in current.get("recipients") or []],
                    "after": [r["name"] for r in recipients],
                }
            )
    if commentable is not None and bool(commentable) != bool(current.get("commentable")):
        payload["commentable"] = bool(commentable)
        changes.append({"field": "commentable", "before": current.get("commentable"), "after": bool(commentable)})
    if acknowledgement is not None:
        achievement = _ack(acknowledgement)
        if achievement != (current.get("achievement") or ""):
            payload["achievement"] = achievement
            changes.append(
                {"field": "acknowledgement", "before": current.get("achievement") or "none", "after": acknowledgement}
            )
    if not payload:
        raise ValueError("Aucun changement par rapport au message actuel.")
    warnings = []
    if current.get("visible"):
        warnings.append(
            f"Message déjà publié : la modification sera visible des familles de "
            f"{len(payload.get('recipients', current.get('recipients') or []))} élève(s)."
        )
        if "recipients" in payload and not payload["recipients"]:
            raise ValueError("Un message publié doit garder au moins un destinataire (le dépublier d'abord).")
    return _wt._gate.prepare(
        _messages_path(classroom_id, message_id),
        {"classroom_id": int(classroom_id)},
        payload,
        summary=f"Modifier le message « {current.get('title')} » ({_status(current)}) de {classroom_name}",
        preview={"classroom": classroom_name, "changes": changes},
        warnings=warnings,
        transport="rest",
        http_method="PUT",
    )


@mcp.tool()
async def edumoov_cahier_prepare_visibility(
    classroom_id: str, message_id: str, action: str, school_id: str | None = None
) -> Any:
    """PRÉPARE la publication ("publish" : visible des familles des élèves
    destinataires, qui sont notifiées) ou la dépublication ("unpublish" : retour
    en brouillon) d'un message du cahier de liaison. Même appel que le bouton de
    l'interface Cartable (PUT {visible})."""
    classroom_name = await _check_classroom(classroom_id, school_id)
    current = await _get_message(classroom_id, message_id)
    if action not in ("publish", "unpublish"):
        raise ValueError("action doit être 'publish' ou 'unpublish'.")
    visible = action == "publish"
    if bool(current.get("visible")) == visible:
        raise ValueError(f"Le message est déjà {_status(current)}.")
    recipients = current.get("recipients") or []
    warnings = []
    if visible:
        if not recipients:
            raise ValueError("Ce message n'a aucun élève destinataire : le compléter d'abord.")
        warnings.append(
            f"Publication IMMÉDIATE : visible et notifiée aux familles de {len(recipients)} "
            f"élève(s) de {classroom_name}."
        )
    else:
        warnings.append("Le message disparaîtra de la vue des familles (les notifications déjà envoyées restent).")
    return _wt._gate.prepare(
        _messages_path(classroom_id, message_id),
        {"classroom_id": int(classroom_id)},
        {"visible": visible},
        summary=(
            f"Message « {current.get('title')} » de {classroom_name} : "
            f"{_status(current)} → {'publié' if visible else 'brouillon'}"
        ),
        preview={"before": _message_summary(current), "after_status": "publié" if visible else "brouillon"},
        warnings=warnings,
        transport="rest",
        http_method="PUT",
    )


@mcp.tool()
async def edumoov_cahier_prepare_delete(
    classroom_id: str, message_id: str, school_id: str | None = None
) -> Any:
    """PRÉPARE la suppression d'un message du cahier de liaison (et de son fil
    de commentaires). Aperçu + jeton."""
    classroom_name = await _check_classroom(classroom_id, school_id)
    current = await _get_message(classroom_id, message_id)
    warnings = []
    if current.get("visible"):
        warnings.append("Message déjà publié : les familles perdront l'accès à son contenu et à ses commentaires.")
    if current.get("comments_count"):
        warnings.append(f"{current.get('comments_count')} commentaire(s) seront perdus avec le message.")
    return _wt._gate.prepare(
        _messages_path(classroom_id, message_id),
        {"classroom_id": int(classroom_id)},
        {},
        summary=f"Supprimer le message « {current.get('title')} » ({_status(current)}) de {classroom_name}",
        preview=_message_summary(current),
        warnings=warnings,
        transport="rest",
        http_method="DELETE",
    )


# ----------------------------------------------------------------------
# Commentaires d'un message du cartable
# ----------------------------------------------------------------------
def _find_comment(comments: list[dict[str, Any]], comment_id: str) -> dict[str, Any] | None:
    for c in comments or []:
        if str(c.get("id")) == str(comment_id):
            return c
        found = _find_comment(c.get("children") or [], comment_id)
        if found:
            return found
    return None


def _author(c: dict[str, Any]) -> str:
    user = c.get("user") or {}
    return f"{user.get('firstname', '')} {user.get('name', '')}".strip() or f"utilisateur {c.get('user_id')}"


@mcp.tool()
async def edumoov_comment_prepare_create(
    classroom_id: str,
    message_id: str,
    body: str,
    reply_to_comment_id: str | None = None,
    school_id: str | None = None,
) -> Any:
    """PRÉPARE un commentaire (réponse) sur un message du cartable — par exemple
    répondre à une famille dans le fil d'un message du cahier de liaison.
    `reply_to_comment_id` : id du commentaire auquel répondre (fil), sinon
    commentaire de premier niveau. Lire d'abord edumoov_cartable_item_comments_list.
    Aperçu + jeton."""
    classroom_name = await _check_classroom(classroom_id, school_id)
    if not body.strip():
        raise ValueError("Le commentaire est vide.")
    if len(body) > COMMENT_BODY_MAX:
        raise ValueError(f"Commentaire trop long ({len(body)} > {COMMENT_BODY_MAX}).")
    message = await _get_message(classroom_id, message_id)
    parent = None
    if reply_to_comment_id is not None:
        thread = await _get_client().list_item_comments(classroom_id, message_id)
        parent = _find_comment(thread, reply_to_comment_id)
        if parent is None or parent.get("deleted"):
            raise ValueError(f"Commentaire {reply_to_comment_id} introuvable dans le fil de ce message.")
    warnings = []
    if message.get("visible"):
        warnings.append(
            "Commentaire visible des familles ayant accès au message "
            f"({len(message.get('recipients') or [])} élève(s) destinataire(s)), qui peuvent être notifiées."
        )
    if not message.get("commentable"):
        warnings.append("Les commentaires sont désactivés sur ce message pour les familles.")
    return _wt._gate.prepare(
        f"core/classroom/{int(classroom_id)}/comments",
        {"classroom_id": int(classroom_id)},
        {
            "key": str(message_id),
            "model": "message",
            "body": body,
            "reply": int(parent["id"]) if parent else None,
        },
        summary=(
            f"Commenter le message « {message.get('title')} » ({_status(message)}) de {classroom_name}"
            + (f", en réponse à {_author(parent)}" if parent else "")
        ),
        preview={
            "message": _message_summary(message),
            "in_reply_to": {"id": parent.get("id"), "author": _author(parent), "body": parent.get("body")}
            if parent
            else None,
            "body": body,
        },
        warnings=warnings,
        transport="rest",
        http_method="POST",
    )


@mcp.tool()
async def edumoov_comment_prepare_delete(
    classroom_id: str, message_id: str, comment_id: str, school_id: str | None = None
) -> Any:
    """PRÉPARE la suppression (mise à la corbeille) d'un commentaire du fil d'un
    message du cartable. `message_id` sert à retrouver et montrer le commentaire.
    Aperçu + jeton."""
    classroom_name = await _check_classroom(classroom_id, school_id)
    message = await _get_message(classroom_id, message_id)
    thread = await _get_client().list_item_comments(classroom_id, message_id)
    comment = _find_comment(thread, comment_id)
    if comment is None or comment.get("deleted"):
        raise ValueError(f"Commentaire {comment_id} introuvable (ou déjà supprimé) dans le fil de ce message.")
    warnings = []
    # Suppression « douce » côté Edumoov (deleted=true reste dans le fil) : ne
    # compter que les réponses encore visibles.
    replies = [c for c in comment.get("children") or [] if not c.get("deleted")]
    if replies:
        warnings.append(f"Ce commentaire a {len(replies)} réponse(s) dans le fil.")
    return _wt._gate.prepare(
        f"core/classroom/{int(classroom_id)}/comments/{int(comment_id)}/trash",
        {"classroom_id": int(classroom_id)},
        {},
        summary=f"Supprimer un commentaire de {_author(comment)} sur « {message.get('title')} » ({classroom_name})",
        preview={"author": _author(comment), "modified": comment.get("modified"), "body": comment.get("body")},
        warnings=warnings,
        transport="rest",
        http_method="DELETE",
    )
